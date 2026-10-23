//! Executable-level acceptance for running `arabica acp` as a
//! Connector-managed stdio child that receives per-session HTTP MCP
//! configuration (the AgentNexus handoff, AR-MCP-01 to AR-MCP-11).
//!
//! Every test drives the real compiled binary over OS pipes. A deterministic
//! OpenAI-compatible model chooses tools from the prompt text, and an audited
//! raw MCP 2026-07-28 fixture records every HTTP request, so assertions are
//! about upstream calls, arguments, and results rather than only ACP
//! `end_turn`.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use agent_client_protocol::schema::ProtocolVersion;
use agent_client_protocol::schema::v1::{
    CancelNotification, CloseSessionRequest, ContentBlock, HttpHeader, InitializeRequest,
    LoadSessionRequest, McpServer, McpServerHttp, NewSessionRequest, PermissionOptionKind,
    PromptRequest, RequestPermissionOutcome, RequestPermissionRequest, RequestPermissionResponse,
    ResumeSessionRequest, SelectedPermissionOutcome, SessionNotification, StopReason,
};
use agent_client_protocol::{ByteStreams, Client, Responder};
use axum::Json;
use axum::body::Bytes;
use axum::extract::{Path as RoutePath, State};
use axum::http::{HeaderMap, Method, StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{any, post};
use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt};
use tokio_util::compat::{TokioAsyncReadCompatExt, TokioAsyncWriteCompatExt};

const MODERN_VERSION: &str = "2026-07-28";

// ---------------------------------------------------------------- model --

/// Chooses a tool from the latest user message (`call <tool> <json>`), and
/// after a tool result answers with that result verbatim, so the test can
/// read what the model was shown.
#[derive(Clone, Default)]
struct Model {
    requests: Arc<Mutex<Vec<Value>>>,
}

fn message_text(message: &Value) -> String {
    match &message["content"] {
        Value::String(text) => text.clone(),
        Value::Array(parts) => parts
            .iter()
            .filter_map(|part| part["text"].as_str())
            .collect::<Vec<_>>()
            .join(""),
        _ => String::new(),
    }
}

async fn chat_completions(State(model): State<Model>, Json(body): Json<Value>) -> Response {
    let index = {
        let mut requests = model.requests.lock().unwrap();
        requests.push(body.clone());
        requests.len()
    };
    let messages = body["messages"].as_array().cloned().unwrap_or_default();
    let last = messages.last().cloned().unwrap_or(Value::Null);
    let message = if last["role"] == "tool" {
        json!({"role": "assistant", "content": format!("TOOL RESULT: {}", message_text(&last))})
    } else {
        let text = message_text(&last);
        match text.strip_prefix("call ") {
            Some(rest) => {
                let (name, arguments) = rest.split_once(' ').unwrap_or((rest, "{}"));
                json!({"role": "assistant", "content": null, "tool_calls": [{
                    "id": format!("call-{index}"), "type": "function",
                    "function": {"name": name, "arguments": arguments}
                }]})
            }
            None => json!({"role": "assistant", "content": "no tool needed"}),
        }
    };
    let finish = if message["tool_calls"].is_array() {
        "tool_calls"
    } else {
        "stop"
    };
    Json(json!({"choices": [{"message": message, "finish_reason": finish}]})).into_response()
}

impl Model {
    async fn start() -> (Self, String) {
        let model = Self::default();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let app = axum::Router::new()
            .route("/v1/chat/completions", post(chat_completions))
            .with_state(model.clone());
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        (model, format!("http://{address}/v1"))
    }

    fn bodies(&self) -> Vec<Value> {
        self.requests.lock().unwrap().clone()
    }

    fn tool_names(&self, request: usize) -> Vec<String> {
        self.bodies()[request]["tools"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|tool| tool["function"]["name"].as_str().map(str::to_owned))
            .collect()
    }

    /// The text of the last assistant answer derived from a tool result.
    fn tool_results(&self) -> Vec<String> {
        self.bodies()
            .iter()
            .filter_map(|body| {
                let last = body["messages"].as_array()?.last()?.clone();
                (last["role"] == "tool").then(|| message_text(&last))
            })
            .collect()
    }
}

// ------------------------------------------------------------ mcp fixture --

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Capability {
    /// Discovery and listing allowed, tool calls denied (setup credentials).
    Setup,
    Active,
    /// After the prompt terminal: everything denied.
    Retired,
    /// Setup credentials rejected outright.
    Unauthorized,
    /// `server/discover` answers after the SDK's 10-second probe deadline.
    SlowDiscovery,
    /// A pre-2026 server: rejects `server/discover`, speaks `initialize`.
    Legacy,
}

#[derive(Clone, Debug)]
struct Recorded {
    capability: String,
    http_method: Method,
    rpc_method: String,
    tool: Option<String>,
    arguments: Value,
    protocol_version: Option<String>,
    has_session_id: bool,
    headers: HashMap<String, String>,
    at: Instant,
}

#[derive(Default)]
struct McpState {
    capabilities: Mutex<HashMap<String, Capability>>,
    /// The Connector-side Task each capability currently serves.
    tasks: Mutex<HashMap<String, String>>,
    requests: Mutex<Vec<Recorded>>,
    /// (capability, tool, arguments, task) for calls that ran to completion.
    executed: Mutex<Vec<(String, String, Value, String)>>,
    other_paths: AtomicUsize,
    hang_started: tokio::sync::Notify,
    hang_dropped: AtomicBool,
}

struct Mcp {
    base: String,
    state: Arc<McpState>,
}

/// Marks a hanging call as abandoned when Hyper drops its handler.
struct DropFlag(Arc<McpState>);

impl Drop for DropFlag {
    fn drop(&mut self) {
        self.0.hang_dropped.store(true, Ordering::SeqCst);
    }
}

async fn other_path(State(state): State<Arc<McpState>>) -> StatusCode {
    state.other_paths.fetch_add(1, Ordering::SeqCst);
    StatusCode::NOT_FOUND
}

async fn mcp_request(
    State(state): State<Arc<McpState>>,
    RoutePath(capability): RoutePath<String>,
    method: Method,
    headers: HeaderMap,
    bytes: Bytes,
) -> Response {
    let request = serde_json::from_slice::<Value>(&bytes).unwrap_or(Value::Null);
    let rpc_method = request["method"].as_str().unwrap_or_default().to_owned();
    let tool = request["params"]["name"].as_str().map(str::to_owned);
    let arguments = request["params"]["arguments"].clone();
    state.requests.lock().unwrap().push(Recorded {
        capability: capability.clone(),
        http_method: method.clone(),
        rpc_method: rpc_method.clone(),
        tool: tool.clone(),
        arguments: arguments.clone(),
        protocol_version: headers
            .get("mcp-protocol-version")
            .and_then(|value| value.to_str().ok())
            .map(str::to_owned),
        has_session_id: headers.contains_key("mcp-session-id"),
        headers: headers
            .iter()
            .filter_map(|(name, value)| {
                Some((name.as_str().to_owned(), value.to_str().ok()?.to_owned()))
            })
            .collect(),
        at: Instant::now(),
    });
    let mode = state.capabilities.lock().unwrap().get(&capability).copied();
    let denied = |reason: &str| (StatusCode::FORBIDDEN, reason.to_owned()).into_response();
    match mode {
        None => return StatusCode::NOT_FOUND.into_response(),
        Some(Capability::Unauthorized) => {
            return (
                StatusCode::UNAUTHORIZED,
                [(header::WWW_AUTHENTICATE, "Bearer error=\"invalid_token\"")],
            )
                .into_response();
        }
        Some(Capability::Retired) => return denied("capability retired"),
        Some(Capability::Setup) if rpc_method == "tools/call" => {
            return denied("setup credentials cannot call tools");
        }
        Some(Capability::SlowDiscovery) if rpc_method == "server/discover" => {
            tokio::time::sleep(Duration::from_secs(11)).await;
        }
        Some(Capability::Legacy) if rpc_method == "server/discover" => {
            return (StatusCode::NOT_FOUND, "this endpoint requires initialize").into_response();
        }
        _ => {}
    }
    if method != Method::POST {
        return StatusCode::METHOD_NOT_ALLOWED.into_response();
    }
    if request.get("id").is_none() {
        return StatusCode::ACCEPTED.into_response();
    }
    let reply = |result: Value| {
        Json(json!({"jsonrpc": "2.0", "id": request["id"], "result": result})).into_response()
    };
    let complete = |text: String, structured: Value, is_error: bool| {
        json!({
            "resultType": "complete",
            "content": [{"type": "text", "text": text}],
            "structuredContent": structured,
            "isError": is_error
        })
    };
    match rpc_method.as_str() {
        "initialize" => reply(json!({
            "protocolVersion": request["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "legacy-fixture", "version": "1"}
        })),
        "server/discover" => reply(json!({
            "resultType": "complete", "supportedVersions": [MODERN_VERSION],
            "capabilities": {"tools": {}}, "ttlMs": 0, "cacheScope": "private",
            "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "agentnexus-fixture", "version": "1"}}
        })),
        "tools/list" if request["params"]["cursor"] == "page-2" => reply(json!({
            "resultType": "complete", "tools": [
                {"name": "rpc_error", "inputSchema": {"type": "object"}},
                {"name": "tool_error", "inputSchema": {"type": "object"}},
                {"name": "http_401", "inputSchema": {"type": "object"}},
                {"name": "http_403", "inputSchema": {"type": "object"}},
                {"name": "http_500", "inputSchema": {"type": "object"}},
                {"name": "slow", "inputSchema": {"type": "object", "properties": {"ms": {"type": "integer"}}}},
                {"name": "hang", "inputSchema": {"type": "object"}}
            ]
        })),
        "tools/list" => reply(json!({
            "resultType": "complete", "nextCursor": "page-2", "tools": [{
                "name": "echo", "description": "Echo a value through the platform",
                "inputSchema": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}
            }]
        })),
        "tools/call" => {
            let tool = tool.unwrap_or_default();
            let task = state
                .tasks
                .lock()
                .unwrap()
                .get(&capability)
                .cloned()
                .unwrap_or_default();
            let executed = |state: &McpState| {
                state.executed.lock().unwrap().push((
                    capability.clone(),
                    tool.clone(),
                    arguments.clone(),
                    task.clone(),
                ));
            };
            match tool.as_str() {
                "echo" => {
                    executed(&state);
                    reply(complete(
                        format!(
                            "echo {} for {task}",
                            arguments["value"].as_str().unwrap_or("")
                        ),
                        json!({"value": arguments["value"], "task": task}),
                        false,
                    ))
                }
                "rpc_error" => Json(json!({
                    "jsonrpc": "2.0", "id": request["id"],
                    "error": {"code": -32603, "message": "upstream rejected the call"}
                }))
                .into_response(),
                "tool_error" => {
                    executed(&state);
                    reply(complete(
                        "business rule denied the change".to_owned(),
                        json!({"reason": "denied"}),
                        true,
                    ))
                }
                "http_401" => (
                    StatusCode::UNAUTHORIZED,
                    [(header::WWW_AUTHENTICATE, "Bearer error=\"invalid_token\"")],
                )
                    .into_response(),
                "http_403" => denied("forbidden by platform policy"),
                "http_500" => {
                    (StatusCode::INTERNAL_SERVER_ERROR, "upstream failure").into_response()
                }
                "slow" => {
                    tokio::time::sleep(Duration::from_millis(
                        arguments["ms"].as_u64().unwrap_or(100),
                    ))
                    .await;
                    executed(&state);
                    reply(complete("slow done".to_owned(), json!({}), false))
                }
                "hang" => {
                    let flag = DropFlag(state.clone());
                    state.hang_started.notify_one();
                    tokio::time::sleep(Duration::from_secs(120)).await;
                    std::mem::forget(flag);
                    reply(complete("never".to_owned(), json!({}), false))
                }
                _ => StatusCode::NOT_FOUND.into_response(),
            }
        }
        _ => StatusCode::NOT_FOUND.into_response(),
    }
}

impl Mcp {
    async fn start() -> Self {
        let state = Arc::new(McpState::default());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let app = axum::Router::new()
            .route("/mcp/session/{capability}", any(mcp_request))
            .fallback(other_path)
            .with_state(state.clone());
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        Self {
            base: format!("http://{address}"),
            state,
        }
    }

    /// Mint an opaque capability URL in `mode`, as the Connector would.
    fn capability(&self, mode: Capability) -> String {
        let capability = format!("cap-{}", uuid::Uuid::now_v7().simple());
        self.state
            .capabilities
            .lock()
            .unwrap()
            .insert(capability.clone(), mode);
        capability
    }

    fn set(&self, capability: &str, mode: Capability) {
        self.state
            .capabilities
            .lock()
            .unwrap()
            .insert(capability.to_owned(), mode);
    }

    fn serve_task(&self, capability: &str, task: &str) {
        self.state
            .tasks
            .lock()
            .unwrap()
            .insert(capability.to_owned(), task.to_owned());
    }

    fn url(&self, capability: &str) -> String {
        format!("{}/mcp/session/{capability}", self.base)
    }

    fn server(&self, capability: &str) -> McpServer {
        McpServer::Http(McpServerHttp::new("agentnexus", self.url(capability)).headers(vec![]))
    }

    fn requests(&self, capability: &str) -> Vec<Recorded> {
        self.state
            .requests
            .lock()
            .unwrap()
            .iter()
            .filter(|request| request.capability == capability)
            .cloned()
            .collect()
    }

    fn calls(&self, capability: &str) -> Vec<Recorded> {
        self.requests(capability)
            .into_iter()
            .filter(|request| request.rpc_method == "tools/call")
            .collect()
    }

    fn executed(&self) -> Vec<(String, String, Value, String)> {
        self.state.executed.lock().unwrap().clone()
    }
}

/// The modern wire: discovery then paginated listing over POST, without an
/// initialize lifecycle or MCP session ID, every request versioned.
fn assert_modern_wire(requests: &[Recorded]) {
    assert!(!requests.is_empty());
    assert_eq!(requests[0].rpc_method, "server/discover");
    for request in requests {
        assert_eq!(request.http_method, Method::POST, "{request:?}");
        assert!(!request.has_session_id, "{request:?}");
        assert!(
            !matches!(
                request.rpc_method.as_str(),
                "initialize" | "notifications/initialized"
            ),
            "legacy lifecycle on a modern server: {request:?}"
        );
        assert_eq!(
            request.protocol_version.as_deref(),
            Some(MODERN_VERSION),
            "{request:?}"
        );
    }
}

// ------------------------------------------------------------- the agent --

struct Agent {
    child: tokio::process::Child,
    transport: Option<
        ByteStreams<
            tokio_util::compat::Compat<tokio::process::ChildStdin>,
            tokio_util::compat::Compat<tokio::io::DuplexStream>,
        >,
    >,
    stdout_violations: Arc<Mutex<Vec<String>>>,
    stderr: Arc<Mutex<Vec<String>>>,
}

fn temp_dir(label: &str) -> PathBuf {
    let root = std::env::temp_dir().join(format!(
        "arabica-acp-acceptance-{label}-{}",
        uuid::Uuid::now_v7().simple()
    ));
    std::fs::create_dir_all(&root).unwrap();
    root
}

const CONFIG: &str = "[providers.mock]\napi_key_env = 'ARABICA_PROVIDER_MOCK_API_KEY'\nbase_url = 'http://unused/v1'\n\n[models.default]\nprovider = 'mock'\nmodel_id = 'test-model'\n\n[blend]\ndefault_model = 'default'\ntool_call_capable_models = ['default']\n";

impl Agent {
    /// `arabica acp`, exactly as a Connector launches it.
    fn spawn(model_url: &str, home: &Path) -> Self {
        Self::spawn_with(model_url, home, false)
    }

    fn spawn_with(model_url: &str, home: &Path, modern_only: bool) -> Self {
        let mut command = tokio::process::Command::new(env!("CARGO_BIN_EXE_arabica"));
        command.arg("acp");
        if modern_only {
            command.args(["--mcp-protocol", "modern"]);
        }
        let mut child = command
            .env("ARABICA_PROVIDER_MOCK_API_KEY", "test-key")
            .env("ARABICA__BASE_URL", model_url)
            .env("ARABICA_HOME", home)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .expect("spawn the real arabica binary");
        let stdin = child.stdin.take().unwrap();
        let stdout = child.stdout.take().unwrap();
        let stderr = child.stderr.take().unwrap();
        let stderr_lines = Arc::new(Mutex::new(Vec::new()));
        tokio::spawn({
            let stderr_lines = stderr_lines.clone();
            async move {
                let mut lines = tokio::io::BufReader::new(stderr).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    stderr_lines.lock().unwrap().push(line);
                }
            }
        });
        // Every stdout line must be one JSON-RPC 2.0 message.
        let violations = Arc::new(Mutex::new(Vec::new()));
        let (mut forward, relayed) = tokio::io::duplex(1 << 20);
        tokio::spawn({
            let violations = violations.clone();
            async move {
                let mut lines = tokio::io::BufReader::new(stdout).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    let valid = serde_json::from_str::<Value>(&line)
                        .is_ok_and(|message| message["jsonrpc"] == "2.0");
                    if !valid {
                        violations.lock().unwrap().push(line.clone());
                    }
                    if forward
                        .write_all(format!("{line}\n").as_bytes())
                        .await
                        .is_err()
                    {
                        break;
                    }
                }
            }
        });
        Self {
            child,
            transport: Some(ByteStreams::new(stdin.compat_write(), relayed.compat())),
            stdout_violations: violations,
            stderr: stderr_lines,
        }
    }

    fn transport(
        &mut self,
    ) -> ByteStreams<
        tokio_util::compat::Compat<tokio::process::ChildStdin>,
        tokio_util::compat::Compat<tokio::io::DuplexStream>,
    > {
        self.transport.take().expect("one connection per agent")
    }

    fn stderr(&self) -> String {
        self.stderr.lock().unwrap().join("\n")
    }

    async fn exits_cleanly(mut self) {
        let status = tokio::time::timeout(Duration::from_secs(10), self.child.wait())
            .await
            .expect("arabica exits once stdin closes")
            .unwrap();
        assert!(status.success(), "exit status {status}");
        assert!(
            self.stdout_violations.lock().unwrap().is_empty(),
            "stdout carried non-JSON-RPC output: {:?}",
            self.stdout_violations.lock().unwrap()
        );
    }
}

#[derive(Clone, Default)]
struct ClientLog {
    permissions: Arc<AtomicUsize>,
    updates: Arc<Mutex<Vec<Value>>>,
}

impl ClientLog {
    /// Statuses of tool-call updates, in order.
    fn tool_statuses(&self) -> Vec<String> {
        self.updates
            .lock()
            .unwrap()
            .iter()
            .filter(|update| {
                matches!(
                    update["update"]["sessionUpdate"].as_str(),
                    Some("tool_call" | "tool_call_update")
                )
            })
            .filter_map(|update| update["update"]["status"].as_str().map(str::to_owned))
            .collect()
    }
}

/// Run `scenario` as a Connector-like ACP client: it approves every
/// permission request once and records every session update.
macro_rules! connector {
    ($agent:expr, $log:expr, |$cx:ident| $body:expr) => {{
        let log: ClientLog = $log.clone();
        let updates = log.updates.clone();
        let permissions = log.permissions.clone();
        let transport = $agent.transport();
        tokio::time::timeout(
            Duration::from_secs(60),
            Client
                .builder()
                .name("agentnexus-connector-test")
                .on_receive_request(
                    async move |request: RequestPermissionRequest,
                                responder: Responder<RequestPermissionResponse>,
                                _connection| {
                        permissions.fetch_add(1, Ordering::SeqCst);
                        let allow = request
                            .options
                            .iter()
                            .find(|option| option.kind == PermissionOptionKind::AllowOnce)
                            .unwrap()
                            .option_id
                            .clone();
                        responder.respond(RequestPermissionResponse::new(
                            RequestPermissionOutcome::Selected(SelectedPermissionOutcome::new(
                                allow,
                            )),
                        ))
                    },
                    agent_client_protocol::on_receive_request!(),
                )
                .on_receive_notification(
                    async move |notification: SessionNotification, _connection| {
                        updates
                            .lock()
                            .unwrap()
                            .push(serde_json::to_value(&notification).unwrap());
                        Ok(())
                    },
                    agent_client_protocol::on_receive_notification!(),
                )
                .connect_with(transport, async move |$cx| $body),
        )
        .await
        .expect("scenario timed out")
    }};
}

fn prompt(session: &agent_client_protocol::schema::v1::SessionId, text: &str) -> PromptRequest {
    PromptRequest::new(session.clone(), vec![ContentBlock::from(text)])
}

async fn setup() -> (Model, String, Mcp, PathBuf, PathBuf) {
    let (model, model_url) = Model::start().await;
    let mcp = Mcp::start().await;
    let home = temp_dir("home");
    std::fs::write(home.join("config.toml"), CONFIG).unwrap();
    let workspace = temp_dir("workspace");
    (model, model_url, mcp, home, workspace)
}

// ---------------------------------------------------------------- tests --

/// AR-MCP-01, AR-MCP-02, AR-MCP-03: initialize, discovery, model-visible
/// tools, and two prompts in one session over the modern wire.
#[tokio::test]
async fn one_session_discovers_tools_and_calls_them_across_two_prompts() {
    let (model, model_url, mcp, home, workspace) = setup().await;
    let capability = mcp.capability(Capability::Active);
    mcp.serve_task(&capability, "task-a");
    let mut agent = Agent::spawn(&model_url, &home);
    let log = ClientLog::default();
    let server = mcp.server(&capability);
    let (initialize, stops) = connector!(agent, log, |cx| {
        let initialize = cx
            .send_request(InitializeRequest::new(ProtocolVersion::V1))
            .block_task()
            .await?;
        let session = cx
            .send_request(NewSessionRequest::new(workspace.clone()).mcp_servers(vec![server]))
            .block_task()
            .await?;
        let first = cx
            .send_request(prompt(
                &session.session_id,
                r#"call mcp__agentnexus__echo {"value":"one"}"#,
            ))
            .block_task()
            .await?;
        let second = cx
            .send_request(prompt(
                &session.session_id,
                r#"call mcp__agentnexus__echo {"value":"two"}"#,
            ))
            .block_task()
            .await?;
        Ok((initialize, (first.stop_reason, second.stop_reason)))
    })
    .unwrap();

    // AR-MCP-01: actual identity and capabilities.
    let info = initialize.agent_info.expect("agent info");
    assert_eq!(info.name, "arabica");
    assert_eq!(info.version, env!("CARGO_PKG_VERSION"));
    let capabilities = &initialize.agent_capabilities;
    assert!(capabilities.mcp_capabilities.http);
    assert!(!capabilities.mcp_capabilities.sse);
    assert!(capabilities.load_session);

    // AR-MCP-02: model-visible tools from both pages, modern wire.
    let tools = model.tool_names(0);
    for name in ["mcp__agentnexus__echo", "mcp__agentnexus__slow"] {
        assert!(tools.contains(&name.to_owned()), "{tools:?}");
    }
    let echo_schema = model.bodies()[0]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .find(|tool| tool["function"]["name"] == "mcp__agentnexus__echo")
        .unwrap()["function"]["parameters"]
        .clone();
    assert_eq!(echo_schema["required"], json!(["value"]));
    let requests = mcp.requests(&capability);
    assert_modern_wire(&requests);
    let methods = requests
        .iter()
        .map(|request| request.rpc_method.as_str())
        .collect::<Vec<_>>();
    // AR-MCP-03: listing happens once for the session, not per prompt.
    assert_eq!(
        methods,
        [
            "server/discover",
            "tools/list",
            "tools/list",
            "tools/call",
            "tools/call"
        ]
    );
    assert_eq!(stops, (StopReason::EndTurn, StopReason::EndTurn));
    let calls = mcp.calls(&capability);
    assert_eq!(calls[0].arguments, json!({"value": "one"}));
    assert_eq!(calls[1].arguments, json!({"value": "two"}));
    let executed = mcp.executed();
    assert_eq!(executed.len(), 2);
    assert_eq!(executed[0].2, json!({"value": "one"}));
    assert_eq!(executed[1].2, json!({"value": "two"}));
    assert_eq!(model.tool_results().len(), 2);
    assert!(model.tool_results()[1].contains("echo two for task-a"));
    // No duplicate registrations in the second prompt's model request.
    let last = model.bodies().len() - 1;
    let mut names = model.tool_names(last);
    let total = names.len();
    names.sort();
    names.dedup();
    assert_eq!(names.len(), total);
    assert_eq!(log.permissions.load(Ordering::SeqCst), 2);
    agent.exits_cleanly().await;
}

/// AR-MCP-04: two platform Tasks reuse the same Session URL; the Connector
/// changes the identity, the Agent changes nothing.
#[tokio::test]
async fn consecutive_tasks_reuse_one_session_url_without_agent_side_changes() {
    let (_model, model_url, mcp, home, workspace) = setup().await;
    let capability = mcp.capability(Capability::Active);
    let mut agent = Agent::spawn(&model_url, &home);
    let log = ClientLog::default();
    let server = mcp.server(&capability);
    let mcp_ref = &mcp;
    let cap = capability.clone();
    connector!(agent, log, |cx| {
        cx.send_request(InitializeRequest::new(ProtocolVersion::V1))
            .block_task()
            .await?;
        let session = cx
            .send_request(NewSessionRequest::new(workspace.clone()).mcp_servers(vec![server]))
            .block_task()
            .await?;
        mcp_ref.serve_task(&cap, "task-a");
        cx.send_request(prompt(
            &session.session_id,
            r#"call mcp__agentnexus__echo {"value":"a"}"#,
        ))
        .block_task()
        .await?;
        mcp_ref.serve_task(&cap, "task-b");
        cx.send_request(prompt(
            &session.session_id,
            r#"call mcp__agentnexus__echo {"value":"b"}"#,
        ))
        .block_task()
        .await?;
        Ok(())
    })
    .unwrap();
    let executed = mcp.executed();
    assert_eq!(
        executed
            .iter()
            .map(|(_, _, arguments, task)| (arguments["value"].as_str().unwrap(), task.as_str()))
            .collect::<Vec<_>>(),
        [("a", "task-a"), ("b", "task-b")]
    );
    let calls = mcp.calls(&capability);
    assert_eq!(calls.len(), 2);
    // Identical headers apart from per-request protocol metadata; no
    // Authorization or platform identity is invented by the Agent.
    for call in &calls {
        assert!(!call.headers.contains_key("authorization"), "{call:?}");
    }
    let strip = |call: &Recorded| {
        let mut headers = call.headers.clone();
        headers.remove("content-length");
        headers
    };
    assert_eq!(strip(&calls[0]), strip(&calls[1]));
    agent.exits_cleanly().await;
}

/// AR-MCP-05: setup credentials allow discovery but not calls; a retired
/// capability fails every call; neither produces an effect or a fake success.
#[tokio::test]
async fn setup_and_retired_capabilities_fail_calls_without_effects() {
    let (model, model_url, mcp, home, workspace) = setup().await;
    let capability = mcp.capability(Capability::Setup);
    let mut agent = Agent::spawn(&model_url, &home);
    let log = ClientLog::default();
    let server = mcp.server(&capability);
    let mcp_ref = &mcp;
    let cap = capability.clone();
    let stops = connector!(agent, log, |cx| {
        cx.send_request(InitializeRequest::new(ProtocolVersion::V1))
            .block_task()
            .await?;
        let session = cx
            .send_request(NewSessionRequest::new(workspace.clone()).mcp_servers(vec![server]))
            .block_task()
            .await?;
        let setup = cx
            .send_request(prompt(
                &session.session_id,
                r#"call mcp__agentnexus__echo {"value":"early"}"#,
            ))
            .block_task()
            .await?;
        mcp_ref.set(&cap, Capability::Retired);
        let retired = cx
            .send_request(prompt(
                &session.session_id,
                r#"call mcp__agentnexus__echo {"value":"late"}"#,
            ))
            .block_task()
            .await?;
        Ok((setup.stop_reason, retired.stop_reason))
    })
    .unwrap();
    assert_eq!(stops, (StopReason::EndTurn, StopReason::EndTurn));
    assert!(mcp.executed().is_empty());
    assert_eq!(mcp.calls(&capability).len(), 2, "each call attempted once");
    assert_eq!(
        log.tool_statuses()
            .iter()
            .filter(|status| *status == "failed")
            .count(),
        2
    );
    assert!(!log.tool_statuses().contains(&"completed".to_owned()));
    for result in model.tool_results() {
        assert!(
            result.contains("403"),
            "the model sees the failure: {result}"
        );
    }
    agent.exits_cleanly().await;
}

/// AR-MCP-06: concurrent sessions with the same server name stay isolated.
#[tokio::test]
async fn concurrent_sessions_with_the_same_server_name_stay_isolated() {
    let (_model, model_url, mcp, home, workspace) = setup().await;
    let first = mcp.capability(Capability::Active);
    let second = mcp.capability(Capability::Active);
    mcp.serve_task(&first, "task-a");
    mcp.serve_task(&second, "task-b");
    let mut agent = Agent::spawn(&model_url, &home);
    let log = ClientLog::default();
    let (server_a, server_b) = (mcp.server(&first), mcp.server(&second));
    connector!(agent, log, |cx| {
        cx.send_request(InitializeRequest::new(ProtocolVersion::V1))
            .block_task()
            .await?;
        let a = cx
            .send_request(NewSessionRequest::new(workspace.clone()).mcp_servers(vec![server_a]))
            .block_task()
            .await?;
        let b = cx
            .send_request(NewSessionRequest::new(workspace.clone()).mcp_servers(vec![server_b]))
            .block_task()
            .await?;
        let in_a = cx.send_request(prompt(
            &a.session_id,
            r#"call mcp__agentnexus__echo {"value":"from-a"}"#,
        ));
        let in_b = cx.send_request(prompt(
            &b.session_id,
            r#"call mcp__agentnexus__echo {"value":"from-b"}"#,
        ));
        let (in_a, in_b) = tokio::join!(in_a.block_task(), in_b.block_task());
        assert_eq!(in_a?.stop_reason, StopReason::EndTurn);
        assert_eq!(in_b?.stop_reason, StopReason::EndTurn);
        Ok(())
    })
    .unwrap();
    let mut executed = mcp
        .executed()
        .into_iter()
        .map(|(cap, _, arguments, task)| {
            (cap, arguments["value"].as_str().unwrap().to_owned(), task)
        })
        .collect::<Vec<_>>();
    executed.sort();
    let mut expected = vec![
        (first.clone(), "from-a".to_owned(), "task-a".to_owned()),
        (second.clone(), "from-b".to_owned(), "task-b".to_owned()),
    ];
    expected.sort();
    assert_eq!(executed, expected);
    agent.exits_cleanly().await;
}

/// AR-MCP-07 and the silence half of AR-MCP-09: a slow, silent call settles
/// before the prompt terminal, and nothing reaches the platform afterwards.
#[tokio::test]
async fn a_slow_silent_call_settles_before_the_terminal_and_nothing_follows() {
    let (model, model_url, mcp, home, workspace) = setup().await;
    let capability = mcp.capability(Capability::Active);
    let mut agent = Agent::spawn(&model_url, &home);
    let log = ClientLog::default();
    let server = mcp.server(&capability);
    let (stop, elapsed, ended) = connector!(agent, log, |cx| {
        cx.send_request(InitializeRequest::new(ProtocolVersion::V1))
            .block_task()
            .await?;
        let session = cx
            .send_request(NewSessionRequest::new(workspace.clone()).mcp_servers(vec![server]))
            .block_task()
            .await?;
        let started = Instant::now();
        let response = cx
            .send_request(prompt(
                &session.session_id,
                r#"call mcp__agentnexus__slow {"ms":3000}"#,
            ))
            .block_task()
            .await?;
        let ended = Instant::now();
        tokio::time::sleep(Duration::from_millis(750)).await;
        Ok((response.stop_reason, started.elapsed(), ended))
    })
    .unwrap();
    assert_eq!(stop, StopReason::EndTurn);
    assert!(elapsed >= Duration::from_secs(3), "{elapsed:?}");
    assert_eq!(mcp.executed().len(), 1);
    assert!(model.tool_results()[0].contains("slow done"));
    let requests = mcp.requests(&capability);
    assert!(
        requests.iter().all(|request| request.at < ended),
        "a request reached the platform after the prompt terminal"
    );
    agent.exits_cleanly().await;
}

/// AR-MCP-08: definitive refusals (HTTP 4xx, JSON-RPC errors, `isError`)
/// reach the model as failed tool results; a 5xx leaves the outcome unknown,
/// so the prompt fails instead of letting the model repeat the call. Every
/// call is attempted once, with no OAuth discovery and no downgrade.
#[tokio::test]
async fn error_classes_stay_failures_without_replay_oauth_or_downgrade() {
    let (model, model_url, mcp, home, workspace) = setup().await;
    let capability = mcp.capability(Capability::Active);
    let mut agent = Agent::spawn(&model_url, &home);
    let log = ClientLog::default();
    let server = mcp.server(&capability);
    let refused = ["http_401", "http_403", "rpc_error", "tool_error"];
    let unknown = connector!(agent, log, |cx| {
        cx.send_request(InitializeRequest::new(ProtocolVersion::V1))
            .block_task()
            .await?;
        let session = cx
            .send_request(NewSessionRequest::new(workspace.clone()).mcp_servers(vec![server]))
            .block_task()
            .await?;
        for tool in refused {
            let response = cx
                .send_request(prompt(
                    &session.session_id,
                    &format!("call mcp__agentnexus__{tool} {{}}"),
                ))
                .block_task()
                .await?;
            assert_eq!(response.stop_reason, StopReason::EndTurn, "{tool}");
        }
        let unknown = cx
            .send_request(prompt(
                &session.session_id,
                "call mcp__agentnexus__http_500 {}",
            ))
            .block_task()
            .await
            .expect_err("an unknown outcome fails the prompt");
        // The session stays usable after the failed prompt.
        let after = cx
            .send_request(prompt(&session.session_id, "just answer"))
            .block_task()
            .await?;
        assert_eq!(after.stop_reason, StopReason::EndTurn);
        Ok(unknown.to_string() + &format!("{:?}", unknown.data))
    })
    .unwrap();
    assert!(
        unknown.contains("outcome is unknown and it was not retried"),
        "{unknown}"
    );
    assert!(!unknown.contains(&capability), "{unknown}");
    let calls = mcp
        .calls(&capability)
        .iter()
        .map(|call| call.tool.clone().unwrap())
        .collect::<Vec<_>>();
    assert_eq!(
        calls,
        [
            "http_401",
            "http_403",
            "rpc_error",
            "tool_error",
            "http_500"
        ],
        "each failing call is attempted exactly once"
    );
    assert_modern_wire(&mcp.requests(&capability));
    assert_eq!(
        mcp.state.other_paths.load(Ordering::SeqCst),
        0,
        "no OAuth metadata discovery"
    );
    let statuses = log.tool_statuses();
    assert_eq!(
        statuses.iter().filter(|status| *status == "failed").count(),
        refused.len() + 1,
        "{statuses:?}"
    );
    assert!(!statuses.contains(&"completed".to_owned()), "{statuses:?}");
    let results = model.tool_results();
    assert_eq!(results.len(), refused.len(), "{results:?}");
    assert!(results[0].contains("HTTP 401"), "{results:?}");
    assert!(results[1].contains("HTTP 403") && results[1].contains("forbidden by platform policy"));
    assert!(
        results[2].contains("upstream rejected the call"),
        "{results:?}"
    );
    assert!(
        results[3].contains("business rule denied the change"),
        "{results:?}"
    );
    assert!(results.iter().all(|result| !result.contains(&capability)));
    agent.exits_cleanly().await;
}
/// AR-MCP-08 at setup: a rejected discovery fails `session/new`, and a
/// discovery slower than the SDK's probe deadline stays modern; neither
/// falls back to the legacy handshake.
#[tokio::test]
async fn rejected_or_slow_discovery_never_downgrades() {
    let (_model, model_url, mcp, home, workspace) = setup().await;
    let unauthorized = mcp.capability(Capability::Unauthorized);
    let slow = mcp.capability(Capability::SlowDiscovery);
    let mut agent = Agent::spawn(&model_url, &home);
    let log = ClientLog::default();
    let (rejected, delayed) = (mcp.server(&unauthorized), mcp.server(&slow));
    let rejected = connector!(agent, log, |cx| {
        cx.send_request(InitializeRequest::new(ProtocolVersion::V1))
            .block_task()
            .await?;
        let rejected = cx
            .send_request(NewSessionRequest::new(workspace.clone()).mcp_servers(vec![rejected]))
            .block_task()
            .await;
        cx.send_request(NewSessionRequest::new(workspace.clone()).mcp_servers(vec![delayed]))
            .block_task()
            .await?;
        Ok(rejected
            .err()
            .map(|error| format!("{error} {:?}", error.data)))
    })
    .unwrap();
    let rejected = rejected.expect("a 401 at discovery fails session/new");
    assert!(!rejected.contains(&unauthorized), "{rejected}");
    let methods = |capability: &str| {
        mcp.requests(capability)
            .into_iter()
            .map(|request| request.rpc_method)
            .collect::<Vec<_>>()
    };
    assert_eq!(methods(&unauthorized), ["server/discover"]);
    assert_eq!(
        methods(&slow),
        ["server/discover", "tools/list", "tools/list"]
    );
    assert_modern_wire(&mcp.requests(&slow));
    let stderr = agent.stderr();
    assert!(!stderr.contains(&unauthorized), "{stderr}");
    agent.exits_cleanly().await;
}

/// The generic protocol switch: by default a legacy server connects through
/// `initialize` after rejecting discovery; `--mcp-protocol modern` refuses it.
#[tokio::test]
async fn legacy_servers_fall_back_by_evidence_and_modern_mode_refuses_them() {
    let (_model, model_url, mcp, home, workspace) = setup().await;
    for strict in [false, true] {
        let legacy = mcp.capability(Capability::Legacy);
        let mut agent = Agent::spawn_with(&model_url, &home, strict);
        let log = ClientLog::default();
        let server = mcp.server(&legacy);
        let workspace = workspace.clone();
        let result = connector!(agent, log, |cx| {
            cx.send_request(InitializeRequest::new(ProtocolVersion::V1))
                .block_task()
                .await?;
            Ok(cx
                .send_request(NewSessionRequest::new(workspace).mcp_servers(vec![server]))
                .block_task()
                .await
                .is_ok())
        })
        .unwrap();
        let methods = mcp
            .requests(&legacy)
            .into_iter()
            .map(|request| request.rpc_method)
            .collect::<Vec<_>>();
        if strict {
            assert!(!result, "modern mode refuses a legacy server");
            assert_eq!(methods, ["server/discover"]);
        } else {
            assert!(result, "auto mode connects a legacy server");
            assert_eq!(
                methods[..3],
                ["server/discover", "initialize", "notifications/initialized"]
            );
        }
        agent.exits_cleanly().await;
    }
}
/// AR-MCP-09: cancellation interrupts the in-flight HTTP call, settles the
/// original prompt as cancelled, and nothing is replayed.
#[tokio::test]
async fn cancel_interrupts_the_call_and_settles_the_prompt_without_replay() {
    let (_model, model_url, mcp, home, workspace) = setup().await;
    let capability = mcp.capability(Capability::Active);
    let mut agent = Agent::spawn(&model_url, &home);
    let log = ClientLog::default();
    let server = mcp.server(&capability);
    let state = mcp.state.clone();
    let stop = connector!(agent, log, |cx| {
        cx.send_request(InitializeRequest::new(ProtocolVersion::V1))
            .block_task()
            .await?;
        let session = cx
            .send_request(NewSessionRequest::new(workspace.clone()).mcp_servers(vec![server]))
            .block_task()
            .await?;
        let pending = cx.send_request(prompt(&session.session_id, "call mcp__agentnexus__hang {}"));
        tokio::time::timeout(Duration::from_secs(10), state.hang_started.notified())
            .await
            .expect("the call reaches the platform");
        cx.send_notification(CancelNotification::new(session.session_id.clone()))?;
        let response = pending.block_task().await?;
        // A later prompt in the same session works and replays nothing.
        cx.send_request(prompt(&session.session_id, "just answer"))
            .block_task()
            .await?;
        Ok(response.stop_reason)
    })
    .unwrap();
    assert_eq!(stop, StopReason::Cancelled);
    tokio::time::sleep(Duration::from_millis(300)).await;
    assert!(
        mcp.state.hang_dropped.load(Ordering::SeqCst),
        "the in-flight HTTP request was abandoned"
    );
    assert_eq!(mcp.calls(&capability).len(), 1, "no replay after cancel");
    agent.exits_cleanly().await;
}

/// AR-MCP-09: stdin EOF during a call ends the process without replaying
/// or continuing background work.
#[tokio::test]
async fn disconnect_mid_call_exits_without_further_platform_calls() {
    let (_model, model_url, mcp, home, workspace) = setup().await;
    let capability = mcp.capability(Capability::Active);
    let mut agent = Agent::spawn(&model_url, &home);
    let log = ClientLog::default();
    let server = mcp.server(&capability);
    let state = mcp.state.clone();
    connector!(agent, log, |cx| {
        cx.send_request(InitializeRequest::new(ProtocolVersion::V1))
            .block_task()
            .await?;
        let session = cx
            .send_request(NewSessionRequest::new(workspace.clone()).mcp_servers(vec![server]))
            .block_task()
            .await?;
        let _pending =
            cx.send_request(prompt(&session.session_id, "call mcp__agentnexus__hang {}"));
        tokio::time::timeout(Duration::from_secs(10), state.hang_started.notified())
            .await
            .expect("the call reaches the platform");
        Ok(())
    })
    .unwrap();
    let mut child = agent.child;
    let status = tokio::time::timeout(Duration::from_secs(10), child.wait())
        .await
        .expect("arabica exits on stdin EOF mid-call")
        .unwrap();
    assert!(status.success(), "exit status {status}");
    tokio::time::sleep(Duration::from_millis(300)).await;
    assert!(mcp.state.hang_dropped.load(Ordering::SeqCst));
    assert_eq!(mcp.calls(&capability).len(), 1);
}

/// AR-MCP-10: after eviction (close, or a new process), load and resume bind
/// the freshly supplied capability; history never resurrects the old one and
/// no prompt is replayed.
#[tokio::test]
async fn restore_binds_the_fresh_capability_and_replays_nothing() {
    let (model, model_url, mcp, home, workspace) = setup().await;
    let original = mcp.capability(Capability::Active);
    let mut agent = Agent::spawn(&model_url, &home);
    let log = ClientLog::default();
    let server = mcp.server(&original);
    let initial_workspace = workspace.clone();
    let session_id = connector!(agent, log, |cx| {
        cx.send_request(InitializeRequest::new(ProtocolVersion::V1))
            .block_task()
            .await?;
        let session = cx
            .send_request(NewSessionRequest::new(initial_workspace).mcp_servers(vec![server]))
            .block_task()
            .await?;
        cx.send_request(prompt(
            &session.session_id,
            r#"call mcp__agentnexus__echo {"value":"before"}"#,
        ))
        .block_task()
        .await?;
        cx.send_request(CloseSessionRequest::new(session.session_id.clone()))
            .block_task()
            .await?;
        Ok(session.session_id)
    })
    .unwrap();
    agent.exits_cleanly().await;
    mcp.set(&original, Capability::Retired);

    for restore in ["load", "resume"] {
        let fresh = mcp.capability(Capability::Active);
        let model_calls = model.bodies().len();
        let mut agent = Agent::spawn(&model_url, &home);
        let server = mcp.server(&fresh);
        let id = session_id.clone();
        let workspace = workspace.clone();
        let model = &model;
        connector!(agent, log, |cx| {
            cx.send_request(InitializeRequest::new(ProtocolVersion::V1))
                .block_task()
                .await?;
            if restore == "load" {
                cx.send_request(
                    LoadSessionRequest::new(id.clone(), workspace).mcp_servers(vec![server]),
                )
                .block_task()
                .await?;
            } else {
                cx.send_request(
                    ResumeSessionRequest::new(id.clone(), workspace).mcp_servers(vec![server]),
                )
                .block_task()
                .await?;
            }
            assert_eq!(
                model.bodies().len(),
                model_calls,
                "{restore} replayed a prompt"
            );
            cx.send_request(prompt(
                &id,
                &format!(r#"call mcp__agentnexus__echo {{"value":"after-{restore}"}}"#),
            ))
            .block_task()
            .await?;
            cx.send_request(CloseSessionRequest::new(id.clone()))
                .block_task()
                .await?;
            Ok(())
        })
        .unwrap();
        agent.exits_cleanly().await;
        assert_eq!(
            mcp.calls(&fresh).len(),
            1,
            "{restore} uses the fresh capability"
        );
        assert_modern_wire(&mcp.requests(&fresh));
    }
    assert_eq!(
        mcp.calls(&original).len(),
        1,
        "the retired capability is never contacted after restore"
    );
    let values = mcp
        .executed()
        .into_iter()
        .map(|(_, _, arguments, _)| arguments["value"].as_str().unwrap().to_owned())
        .collect::<Vec<_>>();
    assert_eq!(values, ["before", "after-load", "after-resume"]);
}

/// AR-MCP-11 and probe sessions: user-config servers keep working beside the
/// session's server, nothing overwrites config, and capability URLs and
/// secret headers stay out of logs, model context, and persisted history.
#[tokio::test]
async fn other_servers_survive_and_capabilities_and_secrets_stay_private() {
    let (model, model_url, mcp, home, workspace) = setup().await;
    let capability = mcp.capability(Capability::Active);
    let other = mcp.capability(Capability::Active);
    let config = format!(
        "{CONFIG}\n[[mcp]]\nname = 'other'\nurl = '{}'\n\n[mcp.headers]\nX-Other-Secret = 'other-secret-value'\n",
        mcp.url(&other)
    );
    std::fs::write(home.join("config.toml"), &config).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(
            home.join("config.toml"),
            std::fs::Permissions::from_mode(0o600),
        )
        .unwrap();
    }
    let mut agent = Agent::spawn(&model_url, &home);
    let log = ClientLog::default();
    let session_server = McpServer::Http(
        McpServerHttp::new("agentnexus", mcp.url(&capability)).headers(vec![HttpHeader::new(
            "X-Session-Secret",
            "session-secret-value",
        )]),
    );
    connector!(agent, log, |cx| {
        cx.send_request(InitializeRequest::new(ProtocolVersion::V1))
            .block_task()
            .await?;
        // A capability-probe session without MCP servers.
        let probe = cx
            .send_request(NewSessionRequest::new(workspace.clone()).mcp_servers(vec![]))
            .block_task()
            .await?;
        cx.send_request(prompt(&probe.session_id, "just answer"))
            .block_task()
            .await?;
        let session = cx
            .send_request(
                NewSessionRequest::new(workspace.clone()).mcp_servers(vec![session_server]),
            )
            .block_task()
            .await?;
        cx.send_request(prompt(
            &session.session_id,
            r#"call mcp__agentnexus__echo {"value":"mine"}"#,
        ))
        .block_task()
        .await?;
        cx.send_request(prompt(
            &session.session_id,
            r#"call mcp__other__echo {"value":"theirs"}"#,
        ))
        .block_task()
        .await?;
        Ok(())
    })
    .unwrap();
    let stderr = agent.stderr();
    agent.exits_cleanly().await;

    assert_eq!(
        std::fs::read_to_string(home.join("config.toml")).unwrap(),
        config,
        "config.toml is never rewritten"
    );
    assert_eq!(
        mcp.calls(&capability)[0].headers["x-session-secret"],
        "session-secret-value"
    );
    assert_eq!(
        mcp.calls(&other)[0].headers["x-other-secret"],
        "other-secret-value"
    );
    // The probe session has no session server, only the user-config one.
    assert!(
        !model
            .tool_names(0)
            .contains(&"mcp__agentnexus__echo".to_owned())
    );
    assert!(model.tool_names(0).contains(&"mcp__other__echo".to_owned()));
    assert!(model.tool_names(1).contains(&"mcp__other__echo".to_owned()));

    let model_traffic = serde_json::to_string(&model.bodies()).unwrap();
    let mut persisted = String::new();
    for entry in walk(&home) {
        if entry.file_name().is_some_and(|name| name == "config.toml") {
            continue;
        }
        persisted.push_str(&String::from_utf8_lossy(&std::fs::read(&entry).unwrap()));
    }
    for (place, text) in [
        ("stderr", &stderr),
        ("model context", &model_traffic),
        ("persisted state", &persisted),
    ] {
        for secret in [
            capability.as_str(),
            other.as_str(),
            "session-secret-value",
            "other-secret-value",
        ] {
            assert!(!text.contains(secret), "{secret} leaked into {place}");
        }
    }
}

fn walk(root: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        for entry in std::fs::read_dir(&directory)
            .into_iter()
            .flatten()
            .flatten()
        {
            let path = entry.path();
            if path.is_dir() {
                pending.push(path);
            } else {
                files.push(path);
            }
        }
    }
    files
}

/// Serialized prompts: an overlapping prompt in the same session is rejected
/// clearly while the first one keeps running to its own terminal.
#[tokio::test]
async fn an_overlapping_prompt_is_rejected_and_the_first_still_settles() {
    let (_model, model_url, mcp, home, workspace) = setup().await;
    let capability = mcp.capability(Capability::Active);
    let mut agent = Agent::spawn(&model_url, &home);
    let log = ClientLog::default();
    let server = mcp.server(&capability);
    let (first, second) = connector!(agent, log, |cx| {
        cx.send_request(InitializeRequest::new(ProtocolVersion::V1))
            .block_task()
            .await?;
        let session = cx
            .send_request(NewSessionRequest::new(workspace.clone()).mcp_servers(vec![server]))
            .block_task()
            .await?;
        let first = cx.send_request(prompt(
            &session.session_id,
            r#"call mcp__agentnexus__slow {"ms":800}"#,
        ));
        tokio::time::sleep(Duration::from_millis(200)).await;
        let second = cx
            .send_request(prompt(&session.session_id, "just answer"))
            .block_task()
            .await;
        Ok((
            first.block_task().await?.stop_reason,
            second.err().map(|error| error.to_string()),
        ))
    })
    .unwrap();
    assert_eq!(first, StopReason::EndTurn);
    let error = second.expect("the overlapping prompt is rejected");
    assert!(error.contains("already in progress"), "{error}");
    assert_eq!(mcp.executed().len(), 1);
    agent.exits_cleanly().await;
}
