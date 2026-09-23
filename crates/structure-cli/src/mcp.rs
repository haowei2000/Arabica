//! MCP tools supplied by an ACP client for one session.

use std::collections::{HashMap, HashSet};
use std::path::Path;

use agent_client_protocol::schema::v1::McpServer;
use rmcp::model::CallToolRequestParams;
use rmcp::service::RunningService;
use rmcp::transport::{StreamableHttpClientTransport, TokioChildProcess};
use rmcp::{RoleClient, ServiceExt};
use structure_model::{ContentBlock, ToolDefinition, ToolResultItem};
use structure_protocol::{RunId, ToolInteractionKind};
use structure_runner::{
    RunnerEnvironment, RunnerError, RunnerOutput, ToolExecutionRequest, ToolExecutionResult,
    truncate_output,
};

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

impl McpTools {
    pub async fn connect(servers: Vec<McpServer>, cwd: &Path) -> Result<Self, String> {
        let mut result = Self::default();
        let mut names = HashSet::new();
        for server in servers {
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
                    // Only the environment explicitly supplied by the ACP
                    // client may contain MCP credentials. Do not leak the
                    // agent's provider key to a client-supplied process.
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
                        std::time::Duration::from_secs(15),
                        ().serve(transport),
                    )
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
                        let key = reqwest::header::HeaderName::from_bytes(header.name.as_bytes())
                            .map_err(|_| {
                            format!(
                                "MCP server '{}' has an invalid HTTP header name",
                                config.name
                            )
                        })?;
                        let value = reqwest::header::HeaderValue::from_str(&header.value).map_err(
                            |_| {
                                format!(
                                    "MCP server '{}' has an invalid HTTP header value",
                                    config.name
                                )
                            },
                        )?;
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
                    let client = tokio::time::timeout(
                        std::time::Duration::from_secs(15),
                        ().serve(transport),
                    )
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
            if !names.insert(name.clone()) {
                return Err(format!("duplicate MCP server name '{name}'"));
            }
            let tools =
                tokio::time::timeout(std::time::Duration::from_secs(15), client.list_all_tools())
                    .await
                    .map_err(|_| format!("MCP server '{name}' tool discovery timed out"))?
                    .map_err(|error| {
                        format!("MCP server '{name}' tool discovery failed: {error}")
                    })?;
            let server_index = result.clients.len();
            for tool in tools {
                let exposed_name = format!("mcp__{}__{}", safe_name(&name), safe_name(&tool.name));
                if tool.name.is_empty() || exposed_name.len() > 64 {
                    return Err(format!(
                        "MCP server '{name}' exposed a tool name that cannot be advertised to the model"
                    ));
                }
                if result.routes.contains_key(&exposed_name) {
                    return Err(format!("MCP tool name collision at '{exposed_name}'"));
                }
                result.routes.insert(
                    exposed_name.clone(),
                    RoutedTool {
                        server: server_index,
                        original_name: tool.name.to_string(),
                    },
                );
                result.definitions.push(ToolDefinition {
                    name: exposed_name,
                    description: format!(
                        "MCP server '{name}': {}",
                        tool.description.as_deref().unwrap_or("No description")
                    ),
                    input_schema: serde_json::Value::Object((*tool.input_schema).clone()),
                    strict: None,
                });
            }
            result.clients.push(client);
        }
        Ok(result)
    }

    pub fn definitions(&self) -> &[ToolDefinition] {
        &self.definitions
    }

    pub fn contains(&self, name: &str) -> bool {
        self.routes.contains_key(name)
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
    fn classify(&self, _call: &structure_model::ToolCallItem) -> ToolInteractionKind {
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
    use axum::Json;
    use axum::extract::State;
    use axum::http::{HeaderMap, StatusCode};
    use axum::response::{IntoResponse, Response};
    use axum::routing::post;
    use serde_json::{Value, json};
    use structure_model::ToolCallItem;

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
