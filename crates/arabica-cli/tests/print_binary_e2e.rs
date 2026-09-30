//! Drives the real, compiled `structure -p` binary as a subprocess against a
//! mock OpenAI-compatible HTTP server, with a real `$ARABICA_HOME` on disk
//! -- the automated counterpart to `acp_binary_e2e.rs`'s wire-protocol proof,
//! but for print mode's persistence and `--continue`/`--resume` instead of
//! the ACP JSON-RPC surface.
//!
//! In particular this is the regression test for the "seed `session.created`"
//! step in `run_task` (`crates/arabica-cli/src/print.rs`): a
//! `FileSessionStore` cannot be constructed before the session_id its own
//! `Command::SessionCreate` dispatch allocates, so nothing observes that
//! first event live -- `run_task` closes the gap with one manual
//! `store.observe(&session_created, ..)` call. If that call were ever
//! deleted, a later `--continue`/`--resume` would restore a history that
//! does not start with `SessionCreated`, which `arabica_session::restore_session`
//! rejects outright (see `crates/arabica-session/src/lib.rs`). This test
//! asserts the stronger, more direct fact -- the file's first event record
//! actually is `session.created` -- rather than only proving restore
//! happens not to fail.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::Duration;

use axum::response::{IntoResponse, Json, Response};
use axum::routing::post;
use serde_json::Value;

async fn chat_completions(Json(_body): Json<Value>) -> Response {
    Json(serde_json::json!({
        "choices": [{
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop"
        }]
    }))
    .into_response()
}

async fn spawn_mock() -> std::net::SocketAddr {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("mock endpoint binds");
    let address = listener.local_addr().expect("mock address exists");
    let app = axum::Router::new().route("/v1/chat/completions", post(chat_completions));
    tokio::spawn(async move {
        axum::serve(listener, app)
            .await
            .expect("mock endpoint serves");
    });
    address
}

fn temp_dir(label: &str) -> PathBuf {
    let unique = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock is valid")
        .as_nanos();
    std::env::temp_dir().join(format!("structure-print-e2e-{label}-{unique}"))
}

/// The one workspace directory's one session file under `$ARABICA_HOME`.
/// Locating it by walking the tree (rather than recomputing the workspace-id
/// hash in the test) keeps this test decoupled from that hash's own
/// implementation, and doubles as the "exactly one session file" assertion.
fn find_session_file(arabica_home: &Path) -> PathBuf {
    let sessions_root = arabica_home.join("sessions");
    let workspace_dirs: Vec<PathBuf> = std::fs::read_dir(&sessions_root)
        .expect("sessions dir exists")
        .map(|entry| entry.expect("dir entry readable").path())
        .collect();
    assert_eq!(
        workspace_dirs.len(),
        1,
        "expected exactly one workspace directory under {}",
        sessions_root.display()
    );
    let session_files: Vec<PathBuf> = std::fs::read_dir(&workspace_dirs[0])
        .expect("workspace dir exists")
        .map(|entry| entry.expect("dir entry readable").path())
        .collect();
    assert_eq!(
        session_files.len(),
        1,
        "expected exactly one session file under {}",
        workspace_dirs[0].display()
    );
    session_files[0].clone()
}

fn read_header(session_file: &Path) -> Value {
    let content = std::fs::read_to_string(session_file).expect("session file readable");
    let header_line = content.lines().next().expect("file has a header line");
    serde_json::from_str(header_line).expect("header line is JSON")
}

fn read_event_records(session_file: &Path) -> Vec<Value> {
    let content = std::fs::read_to_string(session_file).expect("session file readable");
    content
        .lines()
        .skip(1)
        .map(|line| serde_json::from_str(line).expect("line is JSON"))
        .collect()
}

async fn run_structure(
    arabica_home: &Path,
    workspace_root: &Path,
    address: std::net::SocketAddr,
    task: &str,
    extra_args: &[&str],
) -> std::process::Output {
    std::fs::create_dir_all(arabica_home).unwrap();
    std::fs::write(
        arabica_home.join("config.toml"),
        "[providers.mock]\napi_key_env = 'ARABICA_PROVIDER_MOCK_API_KEY'\nbase_url = 'http://unused/v1'\n\n[models.default]\nprovider = 'mock'\nmodel_id = 'test-model'\n\n[blend]\ndefault_model = 'default'\ntool_call_capable_models = ['default']\n",
    )
    .unwrap();
    let output = tokio::time::timeout(
        Duration::from_secs(8),
        tokio::process::Command::new(env!("CARGO_BIN_EXE_arabica"))
            .arg("-p")
            .arg(task)
            .args(extra_args)
            .env("ARABICA_HOME", arabica_home)
            .env("ARABICA_PROVIDER_MOCK_API_KEY", "test-key")
            .env("ARABICA__BASE_URL", format!("http://{address}/v1"))
            .current_dir(workspace_root)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true)
            .output(),
    )
    .await
    .expect("the binary exits within the timeout");
    output.expect("spawning and waiting on the binary does not itself fail")
}

#[tokio::test]
async fn continue_and_resume_append_to_the_same_session_file_across_real_process_runs() {
    let address = spawn_mock().await;
    let arabica_home = temp_dir("home");
    let workspace_root = temp_dir("workspace");
    std::fs::create_dir_all(&workspace_root).expect("workspace root created");

    let first = run_structure(&arabica_home, &workspace_root, address, "first turn", &[]).await;
    assert!(
        first.status.success(),
        "first run must exit 0; stderr: {}",
        String::from_utf8_lossy(&first.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&first.stdout).trim(),
        "ok",
        "text mode's stdout must be exactly the model's final answer"
    );

    let session_file = find_session_file(&arabica_home);
    let header = read_header(&session_file);
    let session_id = header["id"]
        .as_str()
        .expect("header has a string id")
        .to_owned();

    let events_after_first = read_event_records(&session_file);
    assert_eq!(
        events_after_first[0]["envelope"]["event"]["type"], "session.created",
        "the first event record in a fresh session's file must be session.created"
    );
    assert_eq!(events_after_first[0]["envelope"]["sequence"], 1);

    // ACP session/close leaves this same persisted state. Continuing it from
    // print mode must activate it before dispatching the next message.
    let mut suspended = events_after_first.last().expect("run produced events")["envelope"].clone();
    suspended["event_id"] = serde_json::json!(format!("event-{session_id}-suspended"));
    suspended["command_id"] = serde_json::json!("test-suspend");
    suspended["run_id"] = Value::Null;
    suspended["sequence"] = serde_json::json!(events_after_first.len() + 1);
    suspended["event"] = serde_json::json!({"type": "session.suspended"});
    let record = serde_json::json!({
        "record": "event",
        "visibility": "client",
        "envelope": suspended
    });
    writeln!(
        std::fs::OpenOptions::new()
            .append(true)
            .open(&session_file)
            .expect("open session log"),
        "{record}"
    )
    .expect("append suspended event");

    let second = run_structure(
        &arabica_home,
        &workspace_root,
        address,
        "second turn",
        &["--continue"],
    )
    .await;
    assert!(
        second.status.success(),
        "--continue run must exit 0; stderr: {}",
        String::from_utf8_lossy(&second.stderr)
    );

    // --continue must land in the SAME file, not create a second session.
    let session_file_after_continue = find_session_file(&arabica_home);
    assert_eq!(session_file_after_continue, session_file);

    let events_after_continue = read_event_records(&session_file);
    assert!(
        events_after_continue
            .iter()
            .any(|record| { record["envelope"]["event"]["type"] == "session.resumed" })
    );
    assert!(
        events_after_continue.len() > events_after_first.len(),
        "--continue must append new events, not replace the file"
    );
    let sequences: Vec<u64> = events_after_continue
        .iter()
        .map(|record| {
            record["envelope"]["sequence"]
                .as_u64()
                .expect("sequence is a number")
        })
        .collect();
    let expected: Vec<u64> = (1..=events_after_continue.len() as u64).collect();
    assert_eq!(
        sequences, expected,
        "sequence numbers must run 1..=n with no gap or restart across --continue"
    );

    let third = run_structure(
        &arabica_home,
        &workspace_root,
        address,
        "third turn",
        &["--resume", &session_id],
    )
    .await;
    assert!(
        third.status.success(),
        "--resume <id> run must exit 0; stderr: {}",
        String::from_utf8_lossy(&third.stderr)
    );

    let session_file_after_resume = find_session_file(&arabica_home);
    assert_eq!(session_file_after_resume, session_file);
    let events_after_resume = read_event_records(&session_file);
    assert!(
        events_after_resume.len() > events_after_continue.len(),
        "--resume <id> must append new events, not replace the file"
    );
    let sequences: Vec<u64> = events_after_resume
        .iter()
        .map(|record| {
            record["envelope"]["sequence"]
                .as_u64()
                .expect("sequence is a number")
        })
        .collect();
    let expected: Vec<u64> = (1..=events_after_resume.len() as u64).collect();
    assert_eq!(
        sequences, expected,
        "sequence numbers must run 1..=n with no gap or restart across --resume"
    );

    std::fs::remove_dir_all(&arabica_home).ok();
    std::fs::remove_dir_all(&workspace_root).ok();
}
