//! Wire-level fixtures deliberately independent of rmcp's server implementation.

use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll};
use std::time::Duration;

use agent_client_protocol::schema::v1::{HttpHeader, McpServerHttp};
use arabica_model::ToolCallItem;
use axum::Json;
use axum::body::{Body, Bytes};
use axum::extract::State;
use axum::http::{
    HeaderMap, Method, StatusCode,
    header::{CONTENT_TYPE, LOCATION},
};
use axum::response::{IntoResponse, Response};
use axum::routing::any;
use serde_json::{Value, json};
use tokio::io::{AsyncRead, AsyncWriteExt, DuplexStream, ReadBuf};
use tokio::sync::Notify;
use tokio_util::codec::{BytesCodec, FramedRead};
use tokio_util::sync::CancellationToken;

use super::auth::AuthEnvironment;
use super::*;

const MODERN_VERSION: &str = "2026-07-28";
const LEGACY_VERSION: &str = "2025-11-25";
const CONTINUATION: &str = "opaque:continuation/keep-this-verbatim";

#[derive(Clone, Copy)]
enum FixtureMode {
    Modern { sse: bool },
    Legacy { probe_status: StatusCode },
    UnsupportedVersion,
    NoTools,
}

#[derive(Clone)]
struct ObservedRequest {
    method: Method,
    body: Value,
    protocol_version: Option<String>,
    rpc_method: Option<String>,
    rpc_name: Option<String>,
    region: Option<String>,
    accept: Option<String>,
    has_session_id: bool,
    authenticated: bool,
    backend: usize,
}

#[derive(Clone)]
struct FixtureState {
    mode: FixtureMode,
    requests: Arc<Mutex<Vec<ObservedRequest>>>,
    stream_signals: Arc<StreamSignals>,
}

struct HttpFixture {
    url: String,
    requests: Arc<Mutex<Vec<ObservedRequest>>>,
    stream_signals: Arc<StreamSignals>,
    task: tokio::task::JoinHandle<()>,
}

#[derive(Default)]
struct StreamSignals {
    started: Notify,
    closed: CancellationToken,
}

/// Keep the writer open until Hyper drops the request's response body. No final
/// result or EOF is generated, so a completed stream proves client cancellation.
struct PendingSseRead {
    reader: DuplexStream,
    _writer: DuplexStream,
    signals: Arc<StreamSignals>,
    started: bool,
}

impl AsyncRead for PendingSseRead {
    fn poll_read(
        mut self: Pin<&mut Self>,
        context: &mut Context<'_>,
        buffer: &mut ReadBuf<'_>,
    ) -> Poll<std::io::Result<()>> {
        if !self.started {
            self.started = true;
            self.signals.started.notify_one();
        }
        Pin::new(&mut self.reader).poll_read(context, buffer)
    }
}

impl Drop for PendingSseRead {
    fn drop(&mut self) {
        self.signals.closed.cancel();
    }
}

impl HttpFixture {
    async fn start(mode: FixtureMode) -> Self {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind fixture");
        let address = listener.local_addr().expect("fixture address");
        let requests = Arc::new(Mutex::new(Vec::new()));
        let stream_signals = Arc::new(StreamSignals::default());
        let state = FixtureState {
            mode,
            requests: requests.clone(),
            stream_signals: stream_signals.clone(),
        };
        let app = axum::Router::new()
            .route("/mcp", any(http_request))
            .with_state(state);
        let task = tokio::spawn(async move {
            axum::serve(listener, app).await.expect("serve fixture");
        });
        Self {
            url: format!("http://{address}/mcp"),
            requests,
            stream_signals,
            task,
        }
    }

    fn config(&self) -> McpServer {
        McpServer::Http(McpServerHttp::new("remote", self.url.clone()).headers(vec![
            HttpHeader::new("authorization", "Bearer fixture-only"),
            HttpHeader::new("x-fixture-token", "fixture-only"),
        ]))
    }

    fn observed(&self) -> Vec<ObservedRequest> {
        self.requests.lock().expect("observations lock").clone()
    }
}

impl Drop for HttpFixture {
    fn drop(&mut self) {
        self.task.abort();
    }
}

fn header(headers: &HeaderMap, name: &str) -> Option<String> {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned)
}

async fn http_request(
    State(state): State<FixtureState>,
    method: Method,
    headers: HeaderMap,
    bytes: Bytes,
) -> Response {
    let request = serde_json::from_slice::<Value>(&bytes).unwrap_or(Value::Null);
    // The log is observational only. Each POST goes to an alternating backend;
    // neither backend has a session registry or access to any prior request.
    let backend = {
        let mut requests = state.requests.lock().expect("observations lock");
        let backend = requests.len() % 2;
        requests.push(ObservedRequest {
            method: method.clone(),
            body: request.clone(),
            protocol_version: header(&headers, "mcp-protocol-version"),
            rpc_method: header(&headers, "mcp-method"),
            rpc_name: header(&headers, "mcp-name"),
            region: header(&headers, "mcp-param-region"),
            accept: header(&headers, "accept"),
            has_session_id: headers.contains_key("mcp-session-id"),
            authenticated: header(&headers, "x-fixture-token").as_deref() == Some("fixture-only")
                && header(&headers, "authorization").as_deref() == Some("Bearer fixture-only"),
            backend,
        });
        backend
    };
    if method != Method::POST {
        return StatusCode::METHOD_NOT_ALLOWED.into_response();
    }
    let rpc_method = request["method"].as_str().unwrap_or_default();
    if rpc_method == "server/discover" {
        match state.mode {
            FixtureMode::Legacy { probe_status } => {
                return (probe_status, "Legacy endpoint requires initialization").into_response();
            }
            FixtureMode::UnsupportedVersion => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({
                        "jsonrpc": "2.0", "id": request["id"],
                        "error": {
                            "code": -32022, "message": "Unsupported protocol version",
                            "data": {"supported": ["2099-01-01"], "requested": MODERN_VERSION}
                        }
                    })),
                )
                    .into_response();
            }
            FixtureMode::Modern { .. } | FixtureMode::NoTools => {}
        }
    }
    if rpc_method == "notifications/initialized" {
        return StatusCode::ACCEPTED.into_response();
    }
    if matches!(state.mode, FixtureMode::Modern { .. })
        && rpc_method == "tools/call"
        && request["params"]["arguments"]["suspend"] == true
    {
        let (reader, mut writer) = tokio::io::duplex(128);
        writer
            .write_all(b": request remains in progress\r\n\r\n")
            .await
            .expect("write SSE comment");
        let stream = FramedRead::new(
            PendingSseRead {
                reader,
                _writer: writer,
                signals: state.stream_signals,
                started: false,
            },
            BytesCodec::new(),
        );
        return (
            [(CONTENT_TYPE, "text/event-stream")],
            Body::from_stream(stream),
        )
            .into_response();
    }
    let result = match state.mode {
        FixtureMode::Modern { .. } => stateless_result(&request, backend),
        FixtureMode::Legacy { .. } => legacy_result(&request),
        FixtureMode::UnsupportedVersion => None,
        FixtureMode::NoTools if rpc_method == "server/discover" => Some(json!({
            "resultType": "complete", "supportedVersions": [MODERN_VERSION],
            "capabilities": {"resources": {}}, "ttlMs": 0, "cacheScope": "private"
        })),
        FixtureMode::NoTools => None,
    };
    let Some(result) = result else {
        return StatusCode::NOT_FOUND.into_response();
    };
    let response = json!({"jsonrpc": "2.0", "id": request["id"], "result": result});
    if matches!(state.mode, FixtureMode::Modern { sse: true }) && rpc_method == "tools/call" {
        let notification = json!({
            "jsonrpc": "2.0", "method": "notifications/message",
            "params": {"level": "info", "data": "Request is being processed"}
        });
        let stream = format!(
            ": request-scoped keepalive\r\n\r\nevent: message\r\ndata: {notification}\r\n\r\nevent: message\r\ndata: {response}\r\n\r\n"
        );
        return ([(CONTENT_TYPE, "text/event-stream")], stream).into_response();
    }
    Json(response).into_response()
}

fn stateless_result(request: &Value, backend: usize) -> Option<Value> {
    Some(match request["method"].as_str()? {
        "server/discover" => json!({
            "resultType": "complete", "supportedVersions": [MODERN_VERSION],
            "capabilities": {"tools": {}}, "ttlMs": 0, "cacheScope": "private",
            "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "raw-fixture", "version": "1"}}
        }),
        "tools/list" if request["params"]["cursor"] == "second-page" => json!({
            "resultType": "complete", "tools": [
                {"name": "fail", "inputSchema": {"type": "object"}},
                {"name": "invalid_header", "inputSchema": {
                    "type": "object", "properties": {"rows": {
                        "type": "array", "items": {"type": "string", "x-mcp-header": "Row"}
                    }}
                }}
            ]
        }),
        "tools/list" => json!({
            "resultType": "complete", "nextCursor": "second-page", "tools": [{
                "name": "echo", "description": "Echo with explicit continuation",
                "inputSchema": {"type": "object", "properties": {
                    "value": {"type": "string"},
                    "routing": {"type": "object", "properties": {
                        "region": {"type": "string", "x-mcp-header": "Region"}
                    }}
                }}
            }]
        }),
        "tools/call" if request["params"]["name"] == "fail" => json!({
            "resultType": "complete", "content": [{"type": "text", "text": "Expected tool failure"}],
            "structuredContent": {"reason": "fixture"}, "isError": true
        }),
        "tools/call" if request["params"].get("requestState").is_none() => json!({
            "resultType": "input_required", "inputRequests": {}, "requestState": CONTINUATION
        }),
        "tools/call" => json!({
            "resultType": "complete", "content": [{"type": "text", "text": "Completed"}],
            "structuredContent": request["params"]["arguments"].get("structured").cloned()
                .unwrap_or_else(|| json!({"value": request["params"]["arguments"]["value"], "backend": backend})),
            "isError": false
        }),
        _ => return None,
    })
}

fn legacy_result(request: &Value) -> Option<Value> {
    Some(match request["method"].as_str()? {
        "initialize" => json!({
            "protocolVersion": request["params"]["protocolVersion"],
            "capabilities": {"tools": {}}, "serverInfo": {"name": "legacy-fixture", "version": "1"}
        }),
        "tools/list" => json!({"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}),
        "tools/call" => json!({"content": [{"type": "text", "text": "Legacy completed"}]}),
        _ => return None,
    })
}

async fn connect(fixture: &HttpFixture) -> McpTools {
    tokio::time::timeout(
        Duration::from_secs(5),
        McpTools::connect(
            vec![fixture.config()],
            Path::new("/"),
            &AuthEnvironment::default(),
        ),
    )
    .await
    .unwrap_or_else(|_| panic!("fixture connection timed out"))
    .unwrap_or_else(|_| panic!("fixture connection failed"))
}

fn execution_request(name: &str, arguments: Value) -> ToolExecutionRequest {
    ToolExecutionRequest {
        run_id: RunId::new("stateless-run"),
        call: ToolCallItem {
            id: None,
            call_id: format!("call-{name}"),
            name: format!("mcp__remote__{name}"),
            arguments,
            provider_state: None,
        },
    }
}

async fn call(tools: &mut McpTools, name: &str, arguments: Value) -> ToolExecutionResult {
    tokio::time::timeout(
        Duration::from_secs(5),
        tools.execute(execution_request(name, arguments)),
    )
    .await
    .unwrap_or_else(|_| panic!("fixture tool call timed out"))
    .unwrap_or_else(|_| panic!("fixture tool call failed"))
}

fn output_json(response: &ToolExecutionResult) -> Value {
    let text = response
        .output
        .iter()
        .find_map(|output| match output {
            RunnerOutput::Stdout(text) => Some(text),
            _ => None,
        })
        .expect("serialized MCP result");
    serde_json::from_str(text).expect("result JSON")
}

#[tokio::test]
async fn servers_without_tools_do_not_receive_undeclared_discovery_requests() {
    let fixture = HttpFixture::start(FixtureMode::NoTools).await;
    let tools = connect(&fixture).await;
    assert!(tools.definitions().is_empty());
    let requests = fixture.observed();
    assert_eq!(requests.len(), 1);
    assert_eq!(requests[0].body["method"], "server/discover");
}

#[tokio::test]
async fn modern_http_is_self_contained_across_backends_json_sse_and_continuation() {
    for sse in [false, true] {
        let fixture = HttpFixture::start(FixtureMode::Modern { sse }).await;
        let mut tools = connect(&fixture).await;
        assert_eq!(
            tools
                .definitions()
                .iter()
                .map(|tool| tool.name.as_str())
                .collect::<Vec<_>>(),
            ["mcp__remote__echo", "mcp__remote__fail"]
        );
        let arguments = json!({"value": "stateless", "routing": {"region": "eu-west"}});
        let response = call(&mut tools, "echo", arguments.clone()).await;
        assert!(!response.result.is_error);
        assert_eq!(
            output_json(&response)["structuredContent"]["value"],
            "stateless"
        );
        let failure = call(&mut tools, "fail", json!({})).await;
        assert!(failure.result.is_error);
        assert_eq!(output_json(&failure)["isError"], true);
        assert_eq!(
            output_json(&failure)["structuredContent"],
            json!({"reason": "fixture"})
        );
        for structured in [json!(["item", 42, {"nested": true}]), Value::Null] {
            let response = call(&mut tools, "echo", json!({"structured": structured})).await;
            assert_eq!(
                output_json(&response).get("structuredContent"),
                Some(&structured)
            );
        }

        let requests = fixture.observed();
        let methods = requests
            .iter()
            .map(|request| request.body["method"].as_str().expect("RPC method"))
            .collect::<Vec<_>>();
        assert_eq!(
            methods,
            [
                "server/discover",
                "tools/list",
                "tools/list",
                "tools/call",
                "tools/call",
                "tools/call",
                "tools/call",
                "tools/call",
                "tools/call",
                "tools/call"
            ]
        );
        assert_eq!(requests[2].body["params"]["cursor"], "second-page");
        for request in &requests {
            assert_eq!(request.method, Method::POST);
            assert!(!request.has_session_id);
            assert!(
                request.authenticated,
                "configured header must accompany every request"
            );
            assert_eq!(request.protocol_version.as_deref(), Some(MODERN_VERSION));
            assert_eq!(
                request.rpc_method.as_deref(),
                request.body["method"].as_str()
            );
            let meta = &request.body["params"]["_meta"];
            assert_eq!(
                meta["io.modelcontextprotocol/protocolVersion"],
                MODERN_VERSION
            );
            assert_eq!(
                meta["io.modelcontextprotocol/clientInfo"]["name"],
                "arabica"
            );
            assert_eq!(
                meta["io.modelcontextprotocol/clientInfo"]["version"],
                env!("CARGO_PKG_VERSION")
            );
            assert_eq!(
                meta["io.modelcontextprotocol/clientCapabilities"],
                json!({})
            );
            let accept = request.accept.as_deref().expect("Accept header");
            assert!(accept.contains("application/json") && accept.contains("text/event-stream"));
            if request.body["method"] == "tools/call" {
                assert_eq!(
                    request.rpc_name.as_deref(),
                    request.body["params"]["name"].as_str()
                );
            } else {
                assert!(request.rpc_name.is_none());
            }
        }
        assert!(
            requests
                .windows(2)
                .all(|pair| pair[0].backend != pair[1].backend)
        );
        let initial = &requests[3];
        let retry = &requests[4];
        assert_ne!(initial.body["id"], retry.body["id"]);
        assert_eq!(initial.body["params"]["arguments"], arguments);
        assert_eq!(retry.body["params"]["arguments"], arguments);
        assert_eq!(retry.body["params"]["requestState"], CONTINUATION);
        assert_eq!(initial.region.as_deref(), Some("eu-west"));
        assert_eq!(retry.region.as_deref(), Some("eu-west"));
        assert!(requests[5].region.is_none());
        assert_eq!(
            output_json(&response)["structuredContent"]["backend"],
            retry.backend
        );
    }
}

#[tokio::test]
async fn legacy_http_probe_rejections_fall_back_to_initialization() {
    for probe_status in [
        StatusCode::BAD_REQUEST,
        StatusCode::NOT_FOUND,
        StatusCode::UNPROCESSABLE_ENTITY,
    ] {
        let fixture = HttpFixture::start(FixtureMode::Legacy { probe_status }).await;
        let mut tools = connect(&fixture).await;
        let response = call(&mut tools, "echo", json!({})).await;
        assert!(!response.result.is_error);
        assert_eq!(
            output_json(&response)["content"][0]["text"],
            "Legacy completed"
        );
        let requests = fixture.observed();
        assert_eq!(requests[0].body["method"], "server/discover");
        assert_eq!(requests[1].body["method"], "initialize");
        assert_eq!(
            requests[1].body["params"]["protocolVersion"],
            LEGACY_VERSION
        );
        assert!(
            requests
                .iter()
                .any(|request| request.body["method"] == "notifications/initialized")
        );
        assert!(requests.iter().all(|request| request.authenticated));
    }
}

#[tokio::test]
async fn recognized_modern_version_error_does_not_downgrade_to_legacy() {
    let fixture = HttpFixture::start(FixtureMode::UnsupportedVersion).await;
    let result = tokio::time::timeout(
        Duration::from_secs(5),
        McpTools::connect(
            vec![fixture.config()],
            Path::new("/"),
            &AuthEnvironment::default(),
        ),
    )
    .await
    .unwrap_or_else(|_| panic!("version rejection timed out"));
    assert!(result.is_err());
    let requests = fixture.observed();
    assert_eq!(requests.len(), 1);
    assert_eq!(requests[0].body["method"], "server/discover");
}

#[tokio::test]
async fn configured_headers_cannot_override_protocol_metadata() {
    let fixture = HttpFixture::start(FixtureMode::Modern { sse: false }).await;
    for name in [
        "Accept",
        "Content-Type",
        "Mcp-Session-Id",
        "Last-Event-Id",
        "MCP-Protocol-Version",
        "Mcp-Method",
        "Mcp-Name",
        "Mcp-Param-Region",
    ] {
        let config = McpServerHttp::new("remote", fixture.url.clone())
            .headers(vec![HttpHeader::new(name, "fixture-override-value")]);
        let result = McpTools::connect(
            vec![McpServer::Http(config)],
            Path::new("/"),
            &AuthEnvironment::default(),
        )
        .await;
        let error = match result {
            Ok(_) => panic!("protocol-owned header override must be rejected"),
            Err(error) => error,
        };
        assert!(error.contains("protocol-owned HTTP header"));
        assert!(!error.contains("fixture-override-value"));
    }
    assert!(
        fixture.observed().is_empty(),
        "invalid headers must fail before HTTP"
    );
}

#[tokio::test]
async fn cross_origin_redirect_does_not_forward_configured_credentials() {
    for status in [
        StatusCode::TEMPORARY_REDIRECT,
        StatusCode::PERMANENT_REDIRECT,
    ] {
        let target = HttpFixture::start(FixtureMode::Modern { sse: false }).await;
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind redirect fixture");
        let address = listener.local_addr().expect("redirect address");
        let location = target.url.clone();
        let app = axum::Router::new().route(
            "/mcp",
            any(move || {
                let location = location.clone();
                async move { (status, [(LOCATION, location)]) }
            }),
        );
        let task = tokio::spawn(async move {
            axum::serve(listener, app)
                .await
                .expect("serve redirect fixture");
        });
        let source = HttpFixture {
            url: format!("http://{address}/mcp"),
            requests: Arc::new(Mutex::new(Vec::new())),
            stream_signals: Arc::new(StreamSignals::default()),
            task,
        };
        let result = tokio::time::timeout(
            Duration::from_secs(5),
            McpTools::connect(
                vec![source.config()],
                Path::new("/"),
                &AuthEnvironment::default(),
            ),
        )
        .await
        .unwrap_or_else(|_| panic!("redirect rejection timed out"));
        assert!(result.is_err(), "cross-origin redirect must stop");
        assert!(
            target.observed().is_empty(),
            "another origin must receive no request"
        );
    }
}

#[tokio::test]
async fn dropping_a_tool_call_closes_its_sse_stream_and_keeps_other_tools_usable() {
    let fixture = HttpFixture::start(FixtureMode::Modern { sse: false }).await;
    let mut tools = connect(&fixture).await;
    {
        let mut pending =
            Box::pin(tools.execute(execution_request("echo", json!({"suspend": true}))));
        tokio::select! {
            _ = &mut pending => panic!("suspended tool must not complete"),
            started = tokio::time::timeout(Duration::from_secs(5), fixture.stream_signals.started.notified()) => {
                started.expect("SSE response must start");
            }
        }
        drop(pending);
    }
    tokio::time::timeout(
        Duration::from_secs(5),
        fixture.stream_signals.closed.cancelled(),
    )
    .await
    .expect("dropping the call must close its SSE response");
    let response = call(&mut tools, "fail", json!({})).await;
    assert!(response.result.is_error);
    assert_eq!(
        output_json(&response)["structuredContent"],
        json!({"reason": "fixture"})
    );
    let requests = fixture.observed();
    assert_eq!(requests.len(), 5);
    assert_eq!(requests[0].body["method"], "server/discover");
    assert_eq!(requests[1].body["method"], "tools/list");
    assert_eq!(requests[2].body["method"], "tools/list");
    assert_eq!(requests[3].body["method"], "tools/call");
    assert_eq!(requests[3].body["params"]["name"], "echo");
    assert_eq!(requests[4].body["method"], "tools/call");
    assert_eq!(requests[4].body["params"]["name"], "fail");
    assert!(
        requests
            .iter()
            .all(|request| request.method == Method::POST && !request.has_session_id)
    );
}
