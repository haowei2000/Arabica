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
use rmcp::model::CallToolRequestParams;
use rmcp::service::RunningService;
use rmcp::transport::{StreamableHttpClientTransport, TokioChildProcess};
use rmcp::{RoleClient, ServiceExt};

type Client = RunningService<RoleClient, ()>;

#[derive(Debug)]
struct RoutedTool {
    server: usize,
    original_name: String,
}

/// Connections and tool names are scoped to a single ACP session. Nothing in
/// this type is shared with another workspace or persisted with credentials.
#[derive(Debug, Default)]
pub struct McpTools {
    clients: Vec<Client>,
    routes: HashMap<String, RoutedTool>,
    definitions: Vec<ToolDefinition>,
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
async fn connect_server(server: McpServer, cwd: &Path) -> Result<Connected, String> {
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
            let client =
                tokio::time::timeout(std::time::Duration::from_secs(15), ().serve(transport))
                    .await
                    .map_err(|_| format!("MCP server '{}' initialization timed out", config.name))?
                    .map_err(|error| {
                        format!(
                            "MCP server '{}' initialization failed: {error}",
                            config.name
                        )
                    })?;
            (config.name, client)
        }
        McpServer::Http(config) => {
            let mut headers = reqwest::header::HeaderMap::new();
            for header in &config.headers {
                let key = reqwest::header::HeaderName::from_bytes(header.name.as_bytes()).map_err(
                    |_| {
                        format!(
                            "MCP server '{}' has an invalid HTTP header name",
                            config.name
                        )
                    },
                )?;
                let value =
                    reqwest::header::HeaderValue::from_str(&header.value).map_err(|_| {
                        format!(
                            "MCP server '{}' has an invalid HTTP header value",
                            config.name
                        )
                    })?;
                headers.insert(key, value);
            }
            let http_client = reqwest::Client::builder()
                .default_headers(headers)
                .build()
                .map_err(|error| {
                    format!("MCP server '{}' HTTP client failed: {error}", config.name)
                })?;
            let transport = StreamableHttpClientTransport::with_client(
                http_client,
                rmcp::transport::streamable_http_client::StreamableHttpClientTransportConfig::with_uri(config.url.as_str()),
            );
            let client =
                tokio::time::timeout(std::time::Duration::from_secs(15), ().serve(transport))
                    .await
                    .map_err(|_| format!("MCP server '{}' initialization timed out", config.name))?
                    .map_err(|error| {
                        format!(
                            "MCP server '{}' initialization failed: {error}",
                            config.name
                        )
                    })?;
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
    let tools = tokio::time::timeout(std::time::Duration::from_secs(15), client.list_all_tools())
        .await
        .map_err(|_| format!("MCP server '{name}' tool discovery timed out"))?
        .map_err(|error| format!("MCP server '{name}' tool discovery failed: {error}"))?;
    Ok(Connected {
        name,
        client,
        tools,
    })
}

impl McpTools {
    pub async fn connect(servers: Vec<McpServer>, cwd: &Path) -> Result<Self, String> {
        let mut result = Self::default();
        let mut names = HashSet::new();
        for server in servers {
            let connected = connect_server(server, cwd).await?;
            result.register(connected, &mut names)?;
        }
        Ok(result)
    }

    /// Connect as many servers as possible, collecting one diagnostic per
    /// failure: the shape terminal surfaces want, where one broken server
    /// must not take the session down. [`Self::connect`] keeps the strict
    /// all-or-nothing contract the ACP client relies on.
    pub async fn connect_lenient(servers: Vec<McpServer>, cwd: &Path) -> (Self, Vec<String>) {
        let mut result = Self::default();
        let mut names = HashSet::new();
        let mut diagnostics = Vec::new();
        for server in servers {
            match connect_server(server, cwd).await {
                Ok(connected) => {
                    if let Err(error) = result.register(connected, &mut names) {
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
    ) -> Result<(), String> {
        let Connected {
            name,
            client,
            tools,
        } = connected;
        if !names.insert(name.clone()) {
            return Err(format!("duplicate MCP server name '{name}'"));
        }
        let server_index = self.clients.len();
        for tool in tools {
            let exposed_name = format!("mcp__{}__{}", safe_name(&name), safe_name(&tool.name));
            if tool.name.is_empty() || exposed_name.len() > 64 {
                return Err(format!(
                    "MCP server '{name}' exposed a tool name that cannot be advertised to the model"
                ));
            }
            if self.routes.contains_key(&exposed_name) {
                return Err(format!("MCP tool name collision at '{exposed_name}'"));
            }
            self.routes.insert(
                exposed_name.clone(),
                RoutedTool {
                    server: server_index,
                    original_name: tool.name.to_string(),
                },
            );
            self.definitions.push(ToolDefinition {
                name: exposed_name,
                description: format!(
                    "MCP server '{name}': {}",
                    tool.description.as_deref().unwrap_or("No description")
                ),
                input_schema: serde_json::Value::Object((*tool.input_schema).clone()),
                strict: None,
            });
        }
        self.clients.push(client);
        Ok(())
    }

    /// Absorb another set of connections behind this one, re-basing its
    /// server indices; used to combine client-supplied servers with
    /// user-config ones.
    pub fn extend(&mut self, other: McpTools) -> Result<(), String> {
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
                },
            );
        }
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
    Ok(McpTools::connect_lenient(servers, cwd).await)
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
        let result = self.clients[route.server]
            .call_tool(params)
            .await
            .map_err(|error| RunnerError::new(format!("MCP tool call failed: {error}")))?;
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
        let (extra, diagnostics) =
            McpTools::connect_lenient(vec![McpServer::Http(config)], Path::new("/")).await;
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
        let mut tools = McpTools::connect(vec![McpServer::Http(config)], Path::new("/"))
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
