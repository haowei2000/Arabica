//! Drives the real, compiled `structure acp` binary as a subprocess against
//! a mock OpenAI-compatible HTTP server, over its actual stdin/stdout --
//! not the in-process `Channel::duplex()` harness `crates/structure-cli/src/acp/mod.rs`'s
//! own `round_trip` tests use.
//!
//! That in-process harness proves the handler wiring, the spawn/observer/permission
//! concurrency, and the stop-reason mapping. It cannot prove that a real subprocess
//! started the way an editor starts one, talking real newline-delimited JSON-RPC over
//! real OS pipes, to a real HTTP round trip, produces a wire-legal multi-turn
//! conversation. That is what this test is for: two prompts in the same session, so
//! the second model call's request body is exactly the kind of place the B4 bug
//! class (`crates/structure-runtime/src/short_memory.rs`'s `exact_transcript_entries`)
//! would resurface if a regression ever reintroduced it -- the mock server itself
//! rejects an illegal message sequence, rather than the test inspecting the body
//! after the fact, so a regression fails loudly at the point it would also break a
//! real provider.

use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use agent_client_protocol::schema::ProtocolVersion;
use agent_client_protocol::schema::v1::{
    ContentBlock, InitializeRequest, NewSessionRequest, PermissionOptionKind, PromptRequest,
    RequestPermissionOutcome, RequestPermissionRequest, RequestPermissionResponse,
    SelectedPermissionOutcome, StopReason,
};
use agent_client_protocol::{ByteStreams, Client, Responder};
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Json, Response};
use axum::routing::post;
use serde_json::Value;
use tokio::io::AsyncBufReadExt;
use tokio_util::compat::{TokioAsyncReadCompatExt, TokioAsyncWriteCompatExt};

#[derive(Clone, Default)]
struct MockState {
    requests: Arc<Mutex<Vec<Value>>>,
    rejected: Arc<AtomicUsize>,
}

/// Every assistant message with `tool_calls` must be followed immediately
/// by exactly that many `tool`-role messages carrying matching
/// `tool_call_id`s, with nothing else wedged in between. This is the wire
/// contract `HistoryProjection::ExactTranscript` exists to guarantee across
/// a second `session/prompt` in the same session; a violation here is a
/// real B4-class regression, not a test-harness quirk.
fn find_sequencing_violation(messages: &[Value]) -> Option<String> {
    let mut index = 0;
    while index < messages.len() {
        let message = &messages[index];
        let tool_call_ids: Vec<&str> = message
            .get("tool_calls")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|call| call["id"].as_str())
            .collect();
        if tool_call_ids.is_empty() {
            index += 1;
            continue;
        }
        for (offset, expected_id) in tool_call_ids.iter().enumerate() {
            let followup_index = index + 1 + offset;
            let Some(followup) = messages.get(followup_index) else {
                return Some(format!(
                    "assistant tool_calls at index {index} has no message at {followup_index} to answer it"
                ));
            };
            if followup["role"] != "tool" {
                return Some(format!(
                    "expected a tool-role message at index {followup_index} answering the assistant tool_calls at index {index}, found role {:?}",
                    followup["role"]
                ));
            }
            if followup["tool_call_id"].as_str() != Some(*expected_id) {
                return Some(format!(
                    "message at index {followup_index} has tool_call_id {:?}, expected {expected_id:?}",
                    followup["tool_call_id"]
                ));
            }
        }
        index += 1 + tool_call_ids.len();
    }
    None
}

fn tool_call_response(call_id: &str, name: &str, arguments: &str) -> Value {
    serde_json::json!({
        "choices": [{
            "message": {
                "role": "assistant",
                "content": null,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments}
                }]
            },
            "finish_reason": "tool_calls"
        }]
    })
}

fn text_response(text: &str) -> Value {
    serde_json::json!({
        "choices": [{
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop"
        }]
    })
}

async fn chat_completions(State(state): State<MockState>, Json(body): Json<Value>) -> Response {
    let call_index = {
        let mut requests = state.requests.lock().expect("requests lock poisoned");
        requests.push(body.clone());
        requests.len()
    };
    let messages = body["messages"].as_array().cloned().unwrap_or_default();
    if let Some(violation) = find_sequencing_violation(&messages) {
        state.rejected.fetch_add(1, Ordering::SeqCst);
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"error": {"message": violation}})),
        )
            .into_response();
    }

    let response = match call_index {
        1 => tool_call_response(
            "call-1",
            "write_file",
            r#"{"path":"note.txt","content":"hello from the mock model"}"#,
        ),
        2 => text_response("created note.txt"),
        3 => text_response(
            "I created note.txt with \"hello from the mock model\" in the previous turn.",
        ),
        other => {
            state.rejected.fetch_add(1, Ordering::SeqCst);
            return (
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"error": {"message": format!("unexpected call number {other}")}})),
            )
                .into_response();
        }
    };
    Json(response).into_response()
}

fn temp_dir(label: &str) -> PathBuf {
    let unique = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock is valid")
        .as_nanos();
    let root = std::env::temp_dir().join(format!("structure-acp-e2e-{label}-{unique}"));
    std::fs::create_dir_all(&root).expect("dir is created");
    root
}

#[tokio::test]
async fn two_prompt_turns_over_the_real_binary_stay_wire_legal_and_execute_for_real() {
    let state = MockState::default();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("mock endpoint binds");
    let address = listener.local_addr().expect("mock address exists");
    let app = axum::Router::new()
        .route("/v1/chat/completions", post(chat_completions))
        .with_state(state.clone());
    tokio::spawn(async move {
        axum::serve(listener, app)
            .await
            .expect("mock endpoint serves");
    });

    let workspace_root = temp_dir("workspace");
    let structure_home = temp_dir("home");

    let mut child = tokio::process::Command::new(env!("CARGO_BIN_EXE_structure"))
        .arg("acp")
        .env("OPENAI__API_KEY", "test-key")
        .env("OPENAI__BASE_URL", format!("http://{address}/v1"))
        .env("OPENAI__MODEL", "test-model")
        .env("STRUCTURE_HOME", &structure_home)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .expect("spawn the real structure binary");

    let stdin = child.stdin.take().expect("stdin is piped");
    let stdout = child.stdout.take().expect("stdout is piped");
    let stderr = child.stderr.take().expect("stderr is piped");
    let stderr_lines = Arc::new(Mutex::new(Vec::<String>::new()));
    tokio::spawn({
        let stderr_lines = stderr_lines.clone();
        async move {
            let mut lines = tokio::io::BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                stderr_lines
                    .lock()
                    .expect("stderr lines lock poisoned")
                    .push(line);
            }
        }
    });

    let transport = ByteStreams::new(stdin.compat_write(), stdout.compat());
    let workspace_for_client = workspace_root.clone();
    let round_trip = Client
        .builder()
        .name("e2e-test-client")
        .on_receive_request(
            async move |request: RequestPermissionRequest,
                        responder: Responder<RequestPermissionResponse>,
                        _connection| {
                // write_file is Ask by default (acp::permission::default_policy):
                // a real editor's user would see and answer this prompt, so the
                // test stands in for them rather than the agent silently
                // executing an unapproved write.
                let allow_once = request
                    .options
                    .iter()
                    .find(|option| option.kind == PermissionOptionKind::AllowOnce)
                    .expect("the agent must offer an allow-once option")
                    .option_id
                    .clone();
                responder.respond(RequestPermissionResponse::new(
                    RequestPermissionOutcome::Selected(SelectedPermissionOutcome::new(allow_once)),
                ))
            },
            agent_client_protocol::on_receive_request!(),
        )
        .connect_with(transport, async move |cx| {
            cx.send_request(InitializeRequest::new(ProtocolVersion::V1))
                .block_task()
                .await?;
            let session = cx
                .send_request(NewSessionRequest::new(workspace_for_client))
                .block_task()
                .await?;
            let session_id = session.session_id.to_string();
            let first = cx
                .send_request(PromptRequest::new(
                    session.session_id.clone(),
                    vec![ContentBlock::from("create note.txt with some content")],
                ))
                .block_task()
                .await?;
            let second = cx
                .send_request(PromptRequest::new(
                    session.session_id,
                    vec![ContentBlock::from("what did you just create?")],
                ))
                .block_task()
                .await?;
            Ok((first.stop_reason, second.stop_reason, session_id))
        });

    let (first_stop_reason, second_stop_reason, session_id) =
        tokio::time::timeout(Duration::from_secs(8), round_trip)
            .await
            .unwrap_or_else(|_| {
                panic!(
                    "the round trip did not finish in time; stderr so far: {:?}",
                    stderr_lines.lock().expect("stderr lines lock poisoned")
                )
            })
            .unwrap_or_else(|error| {
                panic!(
                    "the round trip returned a JSON-RPC error: {error}; stderr: {:?}",
                    stderr_lines.lock().expect("stderr lines lock poisoned")
                )
            });

    let exit_status = tokio::time::timeout(Duration::from_secs(5), child.wait())
        .await
        .expect("the child exits promptly once its stdin closes")
        .expect("waiting on the child does not itself fail");

    assert_eq!(
        state.rejected.load(Ordering::SeqCst),
        0,
        "the mock server rejected at least one request as wire-illegal; stderr: {:?}",
        stderr_lines.lock().expect("stderr lines lock poisoned")
    );
    assert_eq!(
        state.requests.lock().expect("requests lock poisoned").len(),
        3,
        "expected exactly 3 model calls: the tool call, its follow-up, and the second turn"
    );
    assert_eq!(first_stop_reason, StopReason::EndTurn);
    assert_eq!(second_stop_reason, StopReason::EndTurn);
    assert!(
        exit_status.success(),
        "the binary must exit 0 once the connection ends cleanly"
    );

    let written = std::fs::read_to_string(workspace_root.join("note.txt"))
        .expect("write_file actually ran against the real filesystem");
    assert_eq!(written, "hello from the mock model");

    // The ACP session id is exactly the stringified Structure session id
    // (`acp::AcpState::new_session`), so this round-trips it back rather
    // than re-deriving anything the store itself would not have used.
    let workspace_id = arabica::host::workspace_id_for(&workspace_root);
    let stored = structure_adapters::FileSessionStore::read_session(
        &structure_home,
        &workspace_id,
        &structure_protocol::SessionId::new(session_id),
    )
    .expect("the real binary persisted this session and it is readable back");
    assert!(
        matches!(
            stored.events.first().map(|envelope| &envelope.event),
            Some(structure_protocol::Event::SessionCreated { .. })
        ),
        "the first persisted event must be session.created, got {:?}",
        stored.events.first()
    );
    assert!(
        stored.events.len() > 5,
        "two prompt turns, one with a tool call, must persist more than a handful of events, got {}",
        stored.events.len()
    );

    std::fs::remove_dir_all(&workspace_root).ok();
    std::fs::remove_dir_all(&structure_home).ok();
}

// The binary test above only ever exercises `find_sequencing_violation`
// against a well-formed conversation (`HistoryProjection::ExactTranscript`
// is correct, so it never gets the chance to detect anything). These prove
// the detector itself actually catches the B4 pattern it exists to guard
// against, rather than that assumption resting solely on the happy path
// above never tripping it.
mod sequencing_violation_tests {
    use super::find_sequencing_violation;
    use serde_json::json;

    #[test]
    fn a_well_formed_tool_call_and_result_pair_is_not_a_violation() {
        let messages = vec![
            json!({"role": "user", "content": "do something"}),
            json!({
                "role": "assistant",
                "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]
            }),
            json!({"role": "tool", "tool_call_id": "call-1", "content": "ok"}),
            json!({"role": "assistant", "content": "done"}),
        ];
        assert_eq!(find_sequencing_violation(&messages), None);
    }

    #[test]
    fn the_b4_pattern_is_detected_a_message_wedged_between_the_call_and_its_result() {
        // The exact shape B4 produced: a system/observation message lands
        // between an assistant tool_calls message and the tool message
        // that answers it.
        let messages = vec![
            json!({"role": "user", "content": "do something"}),
            json!({
                "role": "assistant",
                "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]
            }),
            json!({"role": "system", "content": "a wedged observation, not a tool result"}),
            json!({"role": "tool", "tool_call_id": "call-1", "content": "ok"}),
        ];
        let violation =
            find_sequencing_violation(&messages).expect("must detect the wedged message");
        assert!(violation.contains("index 2"));
    }

    #[test]
    fn a_mismatched_tool_call_id_is_detected() {
        let messages = vec![
            json!({
                "role": "assistant",
                "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]
            }),
            json!({"role": "tool", "tool_call_id": "call-2", "content": "answers the wrong call"}),
        ];
        assert!(find_sequencing_violation(&messages).is_some());
    }

    #[test]
    fn a_tool_call_with_no_answer_at_all_is_detected() {
        let messages = vec![json!({
            "role": "assistant",
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]
        })];
        assert!(find_sequencing_violation(&messages).is_some());
    }

    #[test]
    fn multiple_tool_calls_in_one_turn_each_need_their_own_matching_result_in_order() {
        let messages = vec![json!({
            "role": "assistant",
            "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                {"id": "call-2", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
            ]
        })];
        assert!(
            find_sequencing_violation(&messages).is_some(),
            "two tool_calls with zero following tool messages must be flagged"
        );

        let messages = vec![
            json!({
                "role": "assistant",
                "tool_calls": [
                    {"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                    {"id": "call-2", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
                ]
            }),
            json!({"role": "tool", "tool_call_id": "call-1", "content": "a"}),
            json!({"role": "tool", "tool_call_id": "call-2", "content": "b"}),
        ];
        assert_eq!(find_sequencing_violation(&messages), None);
    }
}
