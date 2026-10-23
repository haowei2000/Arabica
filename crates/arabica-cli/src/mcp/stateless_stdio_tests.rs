//! Wire-level coverage for modern stdio MCP and the legacy compatibility path.

use std::path::PathBuf;
use std::time::Duration;

use agent_client_protocol::schema::v1::{EnvVariable, McpServerStdio};
use arabica_model::ToolCallItem;
use serde_json::{Value, json};

use super::auth::AuthEnvironment;
use super::*;

struct Fixture {
    root: tempfile::TempDir,
    binary: PathBuf,
    transcript: PathBuf,
}

impl Fixture {
    async fn compile() -> Self {
        let root = tempfile::Builder::new()
            .prefix("arabica-mcp-stateless-")
            .tempdir()
            .expect("create isolated fixture directory");
        let source = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/mcp_stateless_server.rs");
        let binary = root.path().join(format!(
            "mcp-stateless-server{}",
            std::env::consts::EXE_SUFFIX
        ));
        let output = tokio::process::Command::new("rustc")
            .args(["--edition=2024", "-o"])
            .arg(&binary)
            .arg(source)
            .kill_on_drop(true)
            .output()
            .await
            .expect("rustc is available to compile the MCP fixture");
        assert!(
            output.status.success(),
            "MCP fixture compilation failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        let transcript = root.path().join("requests.jsonl");
        Self {
            root,
            binary,
            transcript,
        }
    }

    fn server(&self, mode: &str) -> McpServer {
        McpServer::Stdio(
            McpServerStdio::new("fixture", self.binary.clone())
                .args(vec![mode.to_owned()])
                .env(vec![
                    EnvVariable::new(
                        "MCP_FIXTURE_TRANSCRIPT",
                        self.transcript.display().to_string(),
                    ),
                    EnvVariable::new("MCP_FIXTURE_TOKEN", "configured-only"),
                ]),
        )
    }

    fn requests(&self) -> Vec<Value> {
        std::fs::read_to_string(&self.transcript)
            .expect("read fixture requests")
            .lines()
            .map(|line| serde_json::from_str(line).expect("request is JSON"))
            .collect()
    }

    async fn wait_for_request(&self, method: &str) -> Value {
        loop {
            let transcript = std::fs::read_to_string(&self.transcript).unwrap_or_default();
            for line in transcript.lines() {
                // A concurrent append may expose an incomplete final line.
                // The final transcript assertion still parses every line.
                if let Ok(request) = serde_json::from_str::<Value>(line)
                    && request["method"] == method
                {
                    return request;
                }
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }
}

fn request(tool: &str, arguments: Value) -> ToolExecutionRequest {
    ToolExecutionRequest {
        run_id: RunId::new("stateless-run"),
        call: ToolCallItem {
            id: None,
            call_id: format!("call-{tool}"),
            name: format!("mcp__fixture__{tool}"),
            arguments,
            provider_state: None,
        },
    }
}

fn result_json(result: &ToolExecutionResult) -> Value {
    let [ContentBlock::Text { text }] = result.result.content.as_slice() else {
        panic!("MCP result must retain its serialized content");
    };
    serde_json::from_str(text).expect("MCP result is JSON")
}

fn assert_modern_metadata(request: &Value) {
    let meta = &request["params"]["_meta"];
    assert_eq!(
        meta["io.modelcontextprotocol/protocolVersion"], "2026-07-28",
        "every modern request declares its protocol version: {request}"
    );
    assert_eq!(
        meta["io.modelcontextprotocol/clientCapabilities"],
        json!({}),
        "the client must not advertise unsupported optional capabilities"
    );
    assert_eq!(
        meta["io.modelcontextprotocol/clientInfo"]["name"],
        "arabica"
    );
    assert_eq!(
        meta["io.modelcontextprotocol/clientInfo"]["version"],
        env!("CARGO_PKG_VERSION")
    );
    assert!(request["params"].get("protocolVersion").is_none());
}

#[tokio::test]
async fn modern_stdio_discovers_pages_and_calls_without_a_handshake() {
    tokio::time::timeout(Duration::from_secs(45), async {
        let fixture = Fixture::compile().await;
        let mut tools = McpTools::connect(
            vec![fixture.server("modern")],
            fixture.root.path(),
            &AuthEnvironment::default(),
        )
        .await
        .expect("a pure stateless stdio peer connects without initialize");
        assert_eq!(
            tools
                .definitions()
                .iter()
                .map(|tool| tool.name.as_str())
                .collect::<Vec<_>>(),
            [
                "mcp__fixture__structured",
                "mcp__fixture__failure",
                "mcp__fixture__environment"
            ]
        );

        let result = tools
            .execute(request("structured", json!({"value": "worked"})))
            .await
            .expect("execute a stateless tool");
        assert!(!result.result.is_error);
        assert_eq!(result.result.call_id, "call-structured");
        let body = result_json(&result);
        assert_eq!(body["resultType"], "complete");
        assert_eq!(body["content"][0]["text"], "structured result");
        assert_eq!(
            body["structuredContent"],
            json!({"value": "worked", "count": 2})
        );

        let failure = tools
            .execute(request("failure", json!({})))
            .await
            .expect("a tool-level error remains a tool result");
        assert!(failure.result.is_error);
        assert_eq!(
            result_json(&failure)["structuredContent"]["code"],
            "fixture_rejected"
        );

        let environment = tools
            .execute(request("environment", json!({})))
            .await
            .expect("execute environment inspection");
        let environment = result_json(&environment);
        assert_eq!(environment["structuredContent"]["token"], "configured-only");
        let names = environment["structuredContent"]["names"]
            .as_array()
            .expect("fixture reports environment names");
        assert!(
            names.iter().all(|name| matches!(
                name.as_str()
                    .expect("environment name is a string")
                    .to_ascii_uppercase()
                    .as_str(),
                "PATH"
                    | "HOME"
                    | "TMPDIR"
                    | "LANG"
                    | "LC_ALL"
                    | "SYSTEMROOT"
                    | "MCP_FIXTURE_TRANSCRIPT"
                    | "MCP_FIXTURE_TOKEN"
            )),
            "the subprocess must receive only allowlisted or configured variables: {names:?}"
        );

        let requests = fixture.requests();
        assert_eq!(
            requests
                .iter()
                .map(|request| request["method"].as_str().unwrap())
                .collect::<Vec<_>>(),
            [
                "server/discover",
                "tools/list",
                "tools/list",
                "tools/call",
                "tools/call",
                "tools/call"
            ]
        );
        for request in &requests {
            assert_modern_metadata(request);
        }
        assert_eq!(requests[2]["params"]["cursor"], "second-page");
        for client in tools.clients.drain(..) {
            client.cancel().await.expect("stop fixture connection");
        }
    })
    .await
    .expect("stateless stdio conformance test completes within its deadline");
}

#[tokio::test]
async fn dropping_a_modern_stdio_call_cancels_its_request_and_preserves_the_connection() {
    tokio::time::timeout(Duration::from_secs(45), async {
        let fixture = Fixture::compile().await;
        let mut tools = McpTools::connect(
            vec![fixture.server("modern")],
            fixture.root.path(),
            &AuthEnvironment::default(),
        )
        .await
        .expect("a stateless peer connects");

        let mut pending = Box::pin(tools.execute(request("structured", json!({"value": "hang"}))));
        let hanging_request = tokio::select! {
            outcome = &mut pending => {
                panic!("the fixture must leave this call pending: {outcome:?}");
            }
            request = fixture.wait_for_request("tools/call") => request,
        };
        assert_eq!(hanging_request["params"]["arguments"]["value"], "hang");
        drop(pending);

        let cancellation = tokio::time::timeout(
            Duration::from_secs(10),
            fixture.wait_for_request("notifications/cancelled"),
        )
        .await
        .expect("dropping the execution future notifies the stdio peer");
        assert!(cancellation.get("id").is_none());
        assert_eq!(
            cancellation["params"]["requestId"], hanging_request["id"],
            "cancellation must identify the exact pending wire request"
        );

        let result = tools
            .execute(request("structured", json!({"value": "after-cancel"})))
            .await
            .expect("another call succeeds over the existing connection");
        assert!(!result.result.is_error);
        assert_eq!(
            result_json(&result)["structuredContent"]["value"],
            "after-cancel"
        );
        let requests = fixture.requests();
        assert_eq!(
            requests
                .iter()
                .map(|request| request["method"].as_str().unwrap())
                .collect::<Vec<_>>(),
            [
                "server/discover",
                "tools/list",
                "tools/list",
                "tools/call",
                "notifications/cancelled",
                "tools/call"
            ],
            "cancellation must not close the connection or repeat discovery"
        );
        assert_ne!(requests[3]["id"], requests[5]["id"]);
        for request in requests
            .iter()
            .filter(|request| request.get("id").is_some())
        {
            assert_modern_metadata(request);
        }
        for client in tools.clients.drain(..) {
            client.cancel().await.expect("stop fixture connection");
        }
    })
    .await
    .expect("stdio cancellation conformance test completes within its deadline");
}

#[tokio::test]
async fn legacy_stdio_falls_back_after_method_not_found() {
    tokio::time::timeout(Duration::from_secs(45), async {
        let fixture = Fixture::compile().await;
        let mut tools = McpTools::connect(
            vec![fixture.server("legacy")],
            fixture.root.path(),
            &AuthEnvironment::default(),
        )
        .await
        .expect("a legacy peer connects after rejecting server/discover");
        let result = tools
            .execute(request("structured", json!({"value": "legacy-worked"})))
            .await
            .expect("execute legacy tool after initialize");
        assert!(!result.result.is_error);
        assert_eq!(
            result_json(&result)["structuredContent"]["value"],
            "legacy-worked"
        );

        let requests = fixture.requests();
        assert_eq!(
            requests
                .iter()
                .map(|request| request["method"].as_str().unwrap())
                .collect::<Vec<_>>(),
            [
                "server/discover",
                "initialize",
                "notifications/initialized",
                "tools/list",
                "tools/list",
                "tools/call"
            ]
        );
        assert_modern_metadata(&requests[0]);
        assert_eq!(requests[1]["params"]["protocolVersion"], "2025-11-25");
        assert_eq!(requests[1]["params"]["capabilities"], json!({}));
        assert_eq!(requests[1]["params"]["clientInfo"]["name"], "arabica");
        assert_eq!(requests[4]["params"]["cursor"], "second-page");
        for request in &requests[3..] {
            assert!(
                request["params"]["_meta"]
                    .get("io.modelcontextprotocol/protocolVersion")
                    .is_none(),
                "legacy operations must retain the negotiated legacy wire format"
            );
        }
        for client in tools.clients.drain(..) {
            client.cancel().await.expect("stop fixture connection");
        }
    })
    .await
    .expect("legacy stdio conformance test completes within its deadline");
}
