//! MCP tools supplied by an ACP client for one session.

use std::collections::{HashMap, HashSet};
use std::path::Path;

use agent_client_protocol::schema::v1::McpServer;
use arabica_model::{ContentBlock, ToolDefinition, ToolResultItem};
use arabica_protocol::{RunId, ToolInteractionKind};
use arabica_runner::{
    RunnerEnvironment, RunnerError, RunnerOutput, ToolExecutionRequest, ToolExecutionResult,
    truncate_output,
};
use rmcp::model::{
    CallToolRequestParams, ClientCapabilities, ClientConfig, Implementation, ProtocolVersion,
};
use rmcp::service::RunningService;
use rmcp::transport::{StreamableHttpClientTransport, TokioChildProcess};
use rmcp::{ClientLifecycleMode, ClientServiceExt, RoleClient};

pub mod auth;
pub mod commands;
pub mod credentials;
pub mod oauth;

use auth::{AuthEnvironment, McpAuth, Origin};

type Client = RunningService<RoleClient, ClientConfig>;

// Stdio: the SDK's ten-second probe deadline plus fifteen seconds for a
// silent legacy server to complete its fallback handshake. HTTP: the whole
// modern probe, or a legacy probe and handshake.
const CONNECTION_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(25);

fn client_config() -> ClientConfig {
    // Advertise only implemented input capabilities. Tool execution and
    // state-only MRTR continuations do not require optional capabilities.
    ClientConfig::new(
        ClientCapabilities::default(),
        Implementation::new("arabica", env!("CARGO_PKG_VERSION")),
    )
}

/// Set once at startup by `arabica acp --mcp-protocol modern`.
static MODERN_ONLY: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// Refuse the legacy `initialize` lifecycle for every MCP server in this
/// process: a server that is not MCP 2026-07-28 fails to connect.
pub fn require_modern_protocol() {
    MODERN_ONLY.store(true, std::sync::atomic::Ordering::Relaxed);
}

fn modern_only() -> bool {
    MODERN_ONLY.load(std::sync::atomic::Ordering::Relaxed)
}

fn modern_lifecycle() -> ClientLifecycleMode {
    ClientLifecycleMode::Discover {
        preferred_versions: vec![ProtocolVersion::V_2026_07_28],
    }
}

fn stdio_lifecycle() -> ClientLifecycleMode {
    if modern_only() {
        return modern_lifecycle();
    }
    // A legacy stdio server may ignore `server/discover` entirely, so the
    // SDK's probe deadline is the only evidence it can give.
    ClientLifecycleMode::Auto {
        preferred_versions: vec![ProtocolVersion::V_2026_07_28],
        legacy_version: Some(ProtocolVersion::LATEST_WITH_INITIALIZE),
    }
}

/// Whether a failed modern probe proves the server is legacy: a correlated
/// JSON-RPC rejection of `server/discover` that is not a modern-era error.
/// Timeouts, transport failures, and HTTP 401/403 prove nothing.
fn is_legacy_evidence(error: &rmcp::service::ClientInitializeError) -> bool {
    use rmcp::model::ErrorCode;
    matches!(
        error,
        rmcp::service::ClientInitializeError::JsonRpcError(data)
            if !matches!(
                data.code,
                ErrorCode::MISSING_REQUIRED_CLIENT_CAPABILITY
                    | ErrorCode::HEADER_MISMATCH
                    | ErrorCode::UNSUPPORTED_PROTOCOL_VERSION
            )
    )
}

#[derive(Debug)]
struct RoutedTool {
    server: usize,
    original_name: String,
    server_id: String,
}

/// Connections and tool names are scoped to a single ACP session. Nothing in
/// this type is shared with another workspace or persisted with credentials.
#[derive(Debug, Default)]
pub struct McpTools {
    clients: Vec<Client>,
    routes: HashMap<String, RoutedTool>,
    definitions: Vec<ToolDefinition>,
    server_ids: std::collections::BTreeSet<String>,
}

/// One server's live connection plus its advertised tools, between
/// transport setup and route registration.
struct Connected {
    name: String,
    client: Client,
    tools: Vec<rmcp::model::Tool>,
}

/// Bring one server up and discover its tools. Error messages carry the
/// server name and the failure, never environment values or header values.
async fn connect_server(
    server: McpServer,
    cwd: &Path,
    origin: Origin,
    environment: &AuthEnvironment,
) -> Result<Connected, String> {
    let (name, client) = match server {
        McpServer::Stdio(config) => {
            if !config.command.is_absolute() {
                return Err(format!(
                    "MCP server '{}' command must be absolute",
                    config.name
                ));
            }
            let mut command = tokio::process::Command::new(&config.command);
            command
                .args(&config.args)
                .current_dir(cwd)
                .kill_on_drop(true)
                .env_clear();
            // Only the environment explicitly supplied with the server may
            // contain MCP credentials. Do not leak the agent's provider
            // key to a server process.
            for key in ["PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT"] {
                if let Some(value) = std::env::var_os(key) {
                    command.env(key, value);
                }
            }
            for variable in &config.env {
                command.env(&variable.name, &variable.value);
            }
            let transport = TokioChildProcess::new(command).map_err(|error| {
                format!("MCP server '{}' could not start: {error}", config.name)
            })?;
            let client = tokio::time::timeout(
                CONNECTION_TIMEOUT,
                client_config().serve_with_lifecycle(transport, stdio_lifecycle()),
            )
            .await
            .map_err(|_| format!("MCP server '{}' connection timed out", config.name))?
            .map_err(|error| format!("MCP server '{}' connection failed: {error}", config.name))?;
            (config.name, client)
        }
        McpServer::Http(config) => {
            let auth = McpAuth::from_meta(&config.name, config.meta.as_ref())?;
            let mut headers = HashMap::new();
            for header in &config.headers {
                let key = reqwest::header::HeaderName::from_bytes(header.name.as_bytes()).map_err(
                    |_| {
                        format!(
                            "MCP server '{}' has an invalid HTTP header name",
                            config.name
                        )
                    },
                )?;
                if matches!(
                    key.as_str(),
                    "accept"
                        | "content-type"
                        | "mcp-session-id"
                        | "last-event-id"
                        | "mcp-protocol-version"
                        | "mcp-method"
                        | "mcp-name"
                ) || key.as_str().starts_with("mcp-param-")
                {
                    return Err(format!(
                        "MCP server '{}' cannot override a protocol-owned HTTP header",
                        config.name
                    ));
                }
                if auth.is_some() && key == reqwest::header::AUTHORIZATION {
                    return Err(format!(
                        "MCP server '{}' cannot combine an Authorization header with dynamic authentication",
                        config.name
                    ));
                }
                let value =
                    reqwest::header::HeaderValue::from_str(&header.value).map_err(|_| {
                        format!(
                            "MCP server '{}' has an invalid HTTP header value",
                            config.name
                        )
                    })?;
                headers.insert(key, value);
            }
            // Keep the SDK's body-preserving, same-origin redirect policy so
            // configured credentials cannot be forwarded to another origin.
            let transport_config = rmcp::transport::streamable_http_client::StreamableHttpClientTransportConfig::with_uri(config.url.as_str())
                .custom_headers(headers);
            // Probe strictly, then fall back only on the server's own legacy
            // evidence. Unlike the SDK's automatic mode, a slow `server/discover`
            // is not treated as legacy: an HTTP legacy server always answers.
            // Each attempt gets a fresh transport.
            macro_rules! serve_http {
                ($transport:expr) => {
                    async {
                        match client_config()
                            .serve_with_lifecycle($transport, modern_lifecycle())
                            .await
                        {
                            Err(error) if !modern_only() && is_legacy_evidence(&error) => {
                                client_config()
                                    .with_protocol_version(ProtocolVersion::LATEST_WITH_INITIALIZE)
                                    .serve_with_lifecycle(
                                        $transport,
                                        ClientLifecycleMode::Initialize,
                                    )
                                    .await
                            }
                            result => result,
                        }
                    }
                };
            }
            let client = match &auth {
                None => {
                    tokio::time::timeout(
                        CONNECTION_TIMEOUT,
                        serve_http!(StreamableHttpClientTransport::from_config(
                            transport_config.clone()
                        )),
                    )
                    .await
                }
                Some(auth) => {
                    let source =
                        auth::source_for(auth, &config.name, &config.url, origin, environment)
                            .await?;
                    let credentials = std::sync::Arc::new(credentials::Credentials::new(source));
                    // Resolve the first credential before the lifecycle
                    // starts, so a slow provider fails on its own deadline
                    // instead of consuming the connection's.
                    credentials
                        .current()
                        .await
                        .map_err(|error| format!("MCP server '{}': {error}", config.name))?;
                    tokio::time::timeout(
                        CONNECTION_TIMEOUT,
                        serve_http!(StreamableHttpClientTransport::with_client(
                            credentials::CredentialedClient::new(credentials.clone()),
                            transport_config.clone(),
                        )),
                    )
                    .await
                }
            }
            .map_err(|_| format!("MCP server '{}' connection timed out", config.name))?
            .map_err(|error| format!("MCP server '{}' connection failed: {error}", config.name))?;
            (config.name, client)
        }
        McpServer::Sse(config) => {
            return Err(format!(
                "MCP server '{}' uses unsupported SSE transport",
                config.name
            ));
        }
        _ => return Err("unsupported MCP transport".to_owned()),
    };
    if name.is_empty() {
        return Err("MCP server name must not be empty".to_owned());
    }
    let tools = if client
        .peer_info()
        .is_some_and(|info| info.capabilities.tools.is_some())
    {
        tokio::time::timeout(std::time::Duration::from_secs(15), client.list_all_tools())
            .await
            .map_err(|_| format!("MCP server '{name}' tool discovery timed out"))?
            .map_err(|error| format!("MCP server '{name}' tool discovery failed: {error}"))?
    } else {
        // A valid server may expose only resources or prompts. Do not send an
        // undeclared tools/list request or fail the entire ACP session.
        Vec::new()
    };
    Ok(Connected {
        name,
        client,
        tools,
    })
}

impl McpTools {
    /// Stable compound identities use original names, never flattened wire names.
    pub fn context_identities(
        &self,
    ) -> std::collections::BTreeMap<String, arabica_protocol::ContextIdentity> {
        let mut identities: std::collections::BTreeMap<_, _> = self
            .definitions
            .iter()
            .filter_map(|tool| {
                self.routes.get(&tool.name).map(|route| {
                    (
                        tool.name.clone(),
                        arabica_runtime::mcp_context_identity(
                            &route.server_id,
                            tool,
                            &route.original_name,
                        ),
                    )
                })
            })
            .collect();
        for server_id in &self.server_ids {
            let id = arabica_runtime::mcp_server_context_id(server_id);
            identities.insert(
                id.clone(),
                arabica_protocol::ContextIdentity {
                    id,
                    kind: arabica_protocol::ContextSourceKind::Mcp,
                    version: None,
                    display_name: server_id.clone(),
                    server_id: Some(server_id.clone()),
                    tool_id: None,
                },
            );
        }
        identities
    }

    pub async fn connect(
        servers: Vec<McpServer>,
        cwd: &Path,
        environment: &AuthEnvironment,
    ) -> Result<Self, String> {
        let mut result = Self::default();
        let mut names = HashSet::new();
        for server in servers {
            let connected = connect_server(server, cwd, Origin::Client, environment).await?;
            result.register(connected, &mut names, "session")?;
        }
        Ok(result)
    }

    /// Connect as many servers as possible, collecting one diagnostic per
    /// failure: the shape terminal surfaces want, where one broken server
    /// must not take the session down. [`Self::connect`] keeps the strict
    /// all-or-nothing contract the ACP client relies on.
    pub async fn connect_lenient(
        servers: Vec<McpServer>,
        cwd: &Path,
        environment: &AuthEnvironment,
    ) -> (Self, Vec<String>) {
        let mut result = Self::default();
        let mut names = HashSet::new();
        let mut diagnostics = Vec::new();
        for server in servers {
            match connect_server(server, cwd, Origin::Configured, environment).await {
                Ok(connected) => {
                    if let Err(error) = result.register(connected, &mut names, "configured") {
                        diagnostics.push(error);
                    }
                }
                Err(error) => diagnostics.push(error),
            }
        }
        (result, diagnostics)
    }

    /// Wire one live server's tools into the route table.
    fn register(
        &mut self,
        connected: Connected,
        names: &mut HashSet<String>,
        origin: &str,
    ) -> Result<(), String> {
        let Connected {
            name,
            client,
            tools,
        } = connected;
        if names.contains(&name) {
            return Err(format!("duplicate MCP server name '{name}'"));
        }
        let server_index = self.clients.len();
        // Validate the whole server before publishing any of its routes.
        let mut routes = HashMap::new();
        let mut definitions = Vec::new();
        for tool in tools {
            let exposed_name = format!("mcp__{}__{}", safe_name(&name), safe_name(&tool.name));
            if tool.name.is_empty() || exposed_name.len() > 64 {
                return Err(format!(
                    "MCP server '{name}' exposed a tool name that cannot be advertised to the model"
                ));
            }
            if self.routes.contains_key(&exposed_name) || routes.contains_key(&exposed_name) {
                return Err(format!("MCP tool name collision at '{exposed_name}'"));
            }
            routes.insert(
                exposed_name.clone(),
                RoutedTool {
                    server: server_index,
                    original_name: tool.name.to_string(),
                    server_id: format!("{origin}:{name}"),
                },
            );
            definitions.push(ToolDefinition {
                name: exposed_name,
                description: format!(
                    "MCP server '{name}': {}",
                    tool.description.as_deref().unwrap_or("No description")
                ),
                input_schema: serde_json::Value::Object((*tool.input_schema).clone()),
                strict: None,
            });
        }
        names.insert(name.clone());
        self.routes.extend(routes);
        self.definitions.extend(definitions);
        self.server_ids.insert(format!("{origin}:{name}"));
        self.clients.push(client);
        Ok(())
    }

    /// Absorb another set of connections behind this one, re-basing its
    /// server indices; used to combine client-supplied servers with
    /// user-config ones.
    pub fn extend(&mut self, other: McpTools) -> Result<(), String> {
        if !self.server_ids.is_disjoint(&other.server_ids) {
            return Err("duplicate MCP server identity".to_owned());
        }
        if let Some(name) = other
            .routes
            .keys()
            .filter(|name| self.routes.contains_key(*name))
            .min()
        {
            return Err(format!("MCP tool name collision at '{name}'"));
        }
        let offset = self.clients.len();
        for (exposed_name, route) in other.routes {
            if self.routes.contains_key(&exposed_name) {
                return Err(format!("MCP tool name collision at '{exposed_name}'"));
            }
            self.routes.insert(
                exposed_name.clone(),
                RoutedTool {
                    server: route.server + offset,
                    original_name: route.original_name,
                    server_id: route.server_id,
                },
            );
        }
        self.server_ids.extend(other.server_ids);
        self.definitions.extend(other.definitions);
        self.clients.extend(other.clients);
        Ok(())
    }

    pub fn definitions(&self) -> &[ToolDefinition] {
        &self.definitions
    }

    pub fn contains(&self, name: &str) -> bool {
        self.routes.contains_key(name)
    }
}

/// A server declaration's display name, whatever transport it uses.
pub fn server_name(server: &McpServer) -> &str {
    match server {
        McpServer::Stdio(config) => &config.name,
        McpServer::Http(config) => &config.name,
        _ => "",
    }
}

/// Connect the MCP servers declared in the user config file
/// (`$ARABICA_HOME/config.toml`), for terminal chat and `-p`. A malformed
/// config is an error; a server that cannot come up becomes a diagnostic
/// and is skipped. Diagnostics name the server and the failure, never the
/// values of its environment variables or headers.
pub async fn connect_configured(
    cwd: &Path,
    arabica_home: &Path,
) -> Result<(McpTools, Vec<String>), String> {
    let servers =
        crate::config::user_config_mcp(arabica_home).map_err(|error| error.to_string())?;
    let environment = AuthEnvironment {
        arabica_home: Some(arabica_home.to_path_buf()),
        broker: None,
    };
    Ok(McpTools::connect_lenient(servers, cwd, &environment).await)
}

/// How a failed `tools/call` ended.
#[derive(Debug, PartialEq, Eq)]
enum CallFailure {
    /// The server answered with a refusal; the call did not run.
    Rejected(String),
    /// No definitive answer (timeout, lost connection, server error); the
    /// call may have run.
    Unknown(String),
}

const MAX_REASON_LENGTH: usize = 300;

fn bounded(text: &str) -> String {
    text.chars().take(MAX_REASON_LENGTH).collect()
}

fn classify_call_failure(error: &rmcp::ServiceError) -> CallFailure {
    use rmcp::transport::streamable_http_client::StreamableHttpError;
    match error {
        rmcp::ServiceError::McpError(error) => CallFailure::Rejected(bounded(&format!(
            "the server returned JSON-RPC error {}: {}",
            error.code.0, error.message
        ))),
        rmcp::ServiceError::TransportSend(transport) => {
            let http = transport
                .error
                .downcast_ref::<StreamableHttpError<reqwest::Error>>();
            match http {
                // A credential that could not be resolved stops the request
                // before it is sent.
                Some(StreamableHttpError::Io(error))
                    if error
                        .get_ref()
                        .is_some_and(|inner| inner.is::<credentials::CredentialError>()) =>
                {
                    CallFailure::Rejected(bounded(&error.to_string()))
                }
                Some(StreamableHttpError::AuthRequired(_)) => {
                    CallFailure::Rejected("the server answered HTTP 401 Unauthorized".to_owned())
                }
                Some(StreamableHttpError::InsufficientScope(_)) => CallFailure::Rejected(
                    "the server answered HTTP 403 Forbidden (insufficient scope)".to_owned(),
                ),
                // rmcp reports other non-success statuses as "HTTP <status>: <body>".
                Some(StreamableHttpError::UnexpectedServerResponse(message))
                    if message.starts_with("HTTP 4") =>
                {
                    CallFailure::Rejected(bounded(&format!("the server answered {message}")))
                }
                Some(StreamableHttpError::Client(error))
                    if error
                        .status()
                        .is_some_and(|status| status.is_client_error()) =>
                {
                    CallFailure::Rejected(format!(
                        "the server answered HTTP {}",
                        error.status().expect("checked above")
                    ))
                }
                _ => CallFailure::Unknown(bounded(&transport.error.to_string())),
            }
        }
        other => CallFailure::Unknown(bounded(&other.to_string())),
    }
}

fn safe_name(value: &str) -> String {
    value
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '_' {
                ch
            } else {
                '_'
            }
        })
        .collect()
}

impl RunnerEnvironment for McpTools {
    fn classify(&self, _call: &arabica_model::ToolCallItem) -> ToolInteractionKind {
        // Server annotations are untrusted hints. Treat all MCP calls as
        // generic and require ACP approval through the default permission rule.
        ToolInteractionKind::Generic
    }

    async fn execute(
        &mut self,
        request: ToolExecutionRequest,
    ) -> Result<ToolExecutionResult, RunnerError> {
        let route = self
            .routes
            .get(&request.call.name)
            .ok_or_else(|| RunnerError::new("unknown MCP tool"))?;
        let arguments = request
            .call
            .arguments
            .as_object()
            .cloned()
            .ok_or_else(|| RunnerError::new("MCP tool arguments must be an object"))?;
        let params =
            CallToolRequestParams::new(route.original_name.clone()).with_arguments(arguments);
        let result = match self.clients[route.server].call_tool(params).await {
            Ok(result) => result,
            Err(error) => match classify_call_failure(&error) {
                // The server refused the request, so the model may see the
                // failure and choose what to do next.
                CallFailure::Rejected(reason) => {
                    let content = format!("MCP tool call failed: {reason}");
                    return Ok(ToolExecutionResult {
                        result: ToolResultItem {
                            id: None,
                            call_id: request.call.call_id,
                            name: Some(request.call.name),
                            content: vec![ContentBlock::text(content.clone())],
                            is_error: true,
                        },
                        output: vec![RunnerOutput::Stderr(content)],
                    });
                }
                // The call may have run. Ending the turn keeps the model
                // loop from repeating a side effect it cannot see.
                CallFailure::Unknown(reason) => {
                    return Err(RunnerError::new(format!(
                        "MCP tool call outcome is unknown and it was not retried: {reason}"
                    )));
                }
            },
        };
        let serialized = serde_json::to_string(&result).map_err(|error| {
            RunnerError::new(format!("MCP tool result could not be serialized: {error}"))
        })?;
        let content = truncate_output(serialized);
        Ok(ToolExecutionResult {
            result: ToolResultItem {
                id: None,
                call_id: request.call.call_id,
                name: Some(request.call.name),
                content: vec![ContentBlock::text(content.clone())],
                is_error: result.is_error.unwrap_or(false),
            },
            output: vec![RunnerOutput::Stdout(content)],
        })
    }

    async fn cancel(&mut self, _run_id: &RunId) -> Result<bool, RunnerError> {
        Ok(false)
    }
}

#[cfg(test)]
pub(crate) mod auth_tests;
#[cfg(test)]
mod stateless_http_tests;
#[cfg(test)]
mod stateless_stdio_tests;

#[cfg(test)]
mod tests {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};

    use agent_client_protocol::schema::v1::{HttpHeader, McpServerHttp};
    use arabica_model::ToolCallItem;
    use axum::Json;
    use axum::extract::State;
    use axum::http::{HeaderMap, StatusCode};
    use axum::response::{IntoResponse, Response};
    use axum::routing::post;
    use serde_json::{Value, json};

    use super::*;

    async fn mcp_http(
        State(saw_header): State<Arc<AtomicBool>>,
        headers: HeaderMap,
        Json(request): Json<Value>,
    ) -> Response {
        if headers
            .get("x-test-token")
            .and_then(|value| value.to_str().ok())
            == Some("expected")
        {
            saw_header.store(true, Ordering::SeqCst);
        }
        let Some(id) = request.get("id") else {
            return StatusCode::ACCEPTED.into_response();
        };
        let result = match request["method"].as_str() {
            Some("initialize") => json!({
                "protocolVersion": request["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "http-fixture", "version": "1.0"}
            }),
            Some("tools/list") => json!({"tools": [{
                "name": "echo", "description": "Echo one value",
                "inputSchema": {"type": "object", "properties": {"value": {"type": "string"}}}
            }]}),
            Some("tools/call") => json!({"content": [{
                "type": "text", "text": request["params"]["arguments"]["value"]
            }]}),
            _ => return StatusCode::NOT_FOUND.into_response(),
        };
        Json(json!({"jsonrpc": "2.0", "id": id, "result": result})).into_response()
    }

    #[test]
    fn only_a_legacy_json_rpc_rejection_counts_as_legacy_evidence() {
        use rmcp::model::{ErrorCode, ErrorData};
        use rmcp::service::ClientInitializeError;
        let rejection =
            |code| ClientInitializeError::JsonRpcError(ErrorData::new(code, "no", None));
        assert!(is_legacy_evidence(&rejection(ErrorCode::METHOD_NOT_FOUND)));
        assert!(is_legacy_evidence(&rejection(ErrorCode::INVALID_REQUEST)));
        for modern in [
            ErrorCode::MISSING_REQUIRED_CLIENT_CAPABILITY,
            ErrorCode::HEADER_MISMATCH,
            ErrorCode::UNSUPPORTED_PROTOCOL_VERSION,
        ] {
            assert!(!is_legacy_evidence(&rejection(modern)));
        }
        assert!(!is_legacy_evidence(
            &ClientInitializeError::ConnectionClosed("lost".to_owned())
        ));
    }

    #[tokio::test]
    async fn a_broken_server_becomes_a_diagnostic_not_a_failure() {
        let (tools, diagnostics) = McpTools::connect_lenient(
            vec![McpServer::Sse(
                agent_client_protocol::schema::v1::McpServerSse::new(
                    "dead",
                    "http://127.0.0.1:1/sse",
                ),
            )],
            Path::new("/"),
            &AuthEnvironment::default(),
        )
        .await;
        assert!(tools.definitions().is_empty());
        assert_eq!(diagnostics.len(), 1);
        assert!(
            diagnostics[0].contains("unsupported SSE transport"),
            "{diagnostics:?}"
        );
    }

    #[tokio::test]
    async fn extend_merges_tools_from_two_servers() {
        let saw_header = Arc::new(AtomicBool::new(false));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let address = listener.local_addr().expect("address");
        let app = axum::Router::new()
            .route("/mcp", post(mcp_http))
            .with_state(saw_header.clone());
        let server = tokio::spawn(async move { axum::serve(listener, app).await.expect("serve") });
        let config = McpServerHttp::new("remote", format!("http://{address}/mcp"));
        let (extra, diagnostics) = McpTools::connect_lenient(
            vec![McpServer::Http(config)],
            Path::new("/"),
            &AuthEnvironment::default(),
        )
        .await;
        assert!(diagnostics.is_empty());
        // The base holds nothing; extending must re-base server indices so
        // the merged route table still executes against the right client.
        let mut merged = McpTools::default();
        merged.extend(extra).expect("merge succeeds");
        assert_eq!(merged.definitions()[0].name, "mcp__remote__echo");
        let response = merged
            .execute(ToolExecutionRequest {
                run_id: RunId::new("run-1"),
                call: ToolCallItem {
                    id: None,
                    call_id: "call-1".to_owned(),
                    name: "mcp__remote__echo".to_owned(),
                    arguments: json!({"value": "merged"}),
                    provider_state: None,
                },
            })
            .await
            .expect("merged tool call succeeds");
        assert!(
            response
                .output
                .iter()
                .any(|item| matches!(item, RunnerOutput::Stdout(text) if text.contains("merged")))
        );
        server.abort();
    }

    #[tokio::test]
    async fn client_supplied_http_server_uses_headers_and_executes_discovered_tool() {
        let saw_header = Arc::new(AtomicBool::new(false));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let address = listener.local_addr().expect("address");
        let app = axum::Router::new()
            .route("/mcp", post(mcp_http))
            .with_state(saw_header.clone());
        let server = tokio::spawn(async move { axum::serve(listener, app).await.expect("serve") });
        let config = McpServerHttp::new("remote", format!("http://{address}/mcp"))
            .headers(vec![HttpHeader::new("x-test-token", "expected")]);
        let mut tools = McpTools::connect(
            vec![McpServer::Http(config)],
            Path::new("/"),
            &AuthEnvironment::default(),
        )
        .await
        .expect("HTTP MCP connects");
        assert_eq!(tools.definitions()[0].name, "mcp__remote__echo");
        let response = tools
            .execute(ToolExecutionRequest {
                run_id: RunId::new("run-1"),
                call: ToolCallItem {
                    id: None,
                    call_id: "call-1".to_owned(),
                    name: "mcp__remote__echo".to_owned(),
                    arguments: json!({"value": "hello"}),
                    provider_state: None,
                },
            })
            .await
            .expect("tool call succeeds");
        assert!(!response.result.is_error);
        assert!(
            response
                .output
                .iter()
                .any(|item| matches!(item, RunnerOutput::Stdout(text) if text.contains("hello")))
        );
        assert!(saw_header.load(Ordering::SeqCst));
        server.abort();
    }
}
