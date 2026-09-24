//! Exercises the real terminal entry point with a mock Chat Completions server.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use axum::Json;
use axum::extract::State;
use axum::routing::post;
use serde_json::{Value, json};
use structure_cli::auth::save_key;
use tokio::io::AsyncWriteExt;
use tokio::sync::Mutex;

fn temp_dir(label: &str) -> PathBuf {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    std::env::temp_dir().join(format!("structure-interactive-{label}-{now}"))
}

async fn run_binary(
    root: &PathBuf,
    home: &PathBuf,
    address: std::net::SocketAddr,
    input: &str,
) -> std::process::Output {
    run_binary_with_args(root, home, address, input, &[]).await
}

async fn run_binary_with_args(
    root: &PathBuf,
    home: &PathBuf,
    address: std::net::SocketAddr,
    input: &str,
    args: &[&str],
) -> std::process::Output {
    let mut child = tokio::process::Command::new(env!("CARGO_BIN_EXE_structure"))
        .args(args)
        .current_dir(root)
        .env("STRUCTURE_HOME", home)
        .env("OPENAI__API_KEY", "test-key")
        .env("OPENAI__BASE_URL", format!("http://{address}/v1"))
        .env("OPENAI__MODEL", "test-model")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(input.as_bytes())
        .await
        .unwrap();
    tokio::time::timeout(Duration::from_secs(10), child.wait_with_output())
        .await
        .unwrap()
        .unwrap()
}

#[tokio::test]
async fn default_command_keeps_two_turns_in_one_session() {
    async fn completion(
        State(requests): State<Arc<Mutex<Vec<Value>>>>,
        Json(body): Json<Value>,
    ) -> Json<Value> {
        let mut requests = requests.lock().await;
        requests.push(body);
        Json(
            json!({"choices":[{"message":{"role":"assistant","content":format!("answer {}", requests.len())},"finish_reason":"stop"}]}),
        )
    }
    let requests = Arc::new(Mutex::new(Vec::new()));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let app = axum::Router::new()
        .route("/v1/chat/completions", post(completion))
        .with_state(Arc::clone(&requests));
    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    let root = temp_dir("turns-root");
    let home = temp_dir("turns-home");
    std::fs::create_dir_all(&root).unwrap();
    let result = run_binary(&root, &home, address, "first\nsecond\n/exit\n").await;
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let stdout = String::from_utf8_lossy(&result.stdout);
    assert!(stdout.contains("answer 1"), "{stdout}");
    assert!(stdout.contains("answer 2"), "{stdout}");
    let continued =
        run_binary_with_args(&root, &home, address, "third\n/exit\n", &["--continue"]).await;
    assert!(
        continued.status.success(),
        "{}",
        String::from_utf8_lossy(&continued.stderr)
    );
    assert!(String::from_utf8_lossy(&continued.stdout).contains("answer 3"));
    let requests = requests.lock().await;
    assert_eq!(requests.len(), 3);
    for prior in ["first", "answer 1", "second", "answer 2"] {
        assert!(
            requests[2]["messages"]
                .as_array()
                .unwrap()
                .iter()
                .any(|message| message["content"].as_str() == Some(prior)),
            "missing {prior}"
        );
    }
    std::fs::remove_dir_all(root).ok();
    std::fs::remove_dir_all(home).ok();
}

#[tokio::test]
async fn terminal_commands_change_the_next_model_request() {
    async fn completion(
        State(requests): State<Arc<Mutex<Vec<Value>>>>,
        Json(body): Json<Value>,
    ) -> Json<Value> {
        requests.lock().await.push(body);
        Json(
            json!({"choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}),
        )
    }
    let requests = Arc::new(Mutex::new(Vec::new()));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let app = axum::Router::new()
        .route("/v1/chat/completions", post(completion))
        .with_state(Arc::clone(&requests));
    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    let root = temp_dir("config-root");
    let home = temp_dir("config-home");
    std::fs::create_dir_all(&root).unwrap();
    let result = run_binary(
        &root,
        &home,
        address,
        "/model other-model\n/thinking on\nhello\n/exit\n",
    )
    .await;
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let second = run_binary(&root, &home, address, "again\n/exit\n").await;
    assert!(
        second.status.success(),
        "{}",
        String::from_utf8_lossy(&second.stderr)
    );
    let requests = requests.lock().await;
    assert_eq!(requests.len(), 2);
    for request in requests.iter() {
        assert_eq!(request["model"], "other-model");
        assert_eq!(request["thinking"]["type"], "enabled");
    }
    let saved = std::fs::read_to_string(
        home.join("workspaces")
            .read_dir()
            .unwrap()
            .next()
            .unwrap()
            .unwrap()
            .path()
            .join("config.toml"),
    )
    .unwrap();
    assert!(saved.contains("model = \"other-model\""));
    assert!(saved.contains("thinking = \"on\""));
    assert!(!saved.contains("test-key"));
    std::fs::remove_dir_all(root).ok();
    std::fs::remove_dir_all(home).ok();
}

#[tokio::test]
async fn interactive_write_requires_a_terminal_approval() {
    async fn completion(Json(body): Json<Value>) -> Json<Value> {
        let has_tool_result = body["messages"]
            .as_array()
            .unwrap()
            .iter()
            .any(|message| message["role"] == "tool");
        if has_tool_result {
            Json(
                json!({"choices":[{"message":{"role":"assistant","content":"written"},"finish_reason":"stop"}]}),
            )
        } else {
            Json(
                json!({"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-1","type":"function","function":{"name":"write_file","arguments":"{\"path\":\"note.txt\",\"content\":\"hello\"}"}}]},"finish_reason":"tool_calls"}]}),
            )
        }
    }
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(
            listener,
            axum::Router::new().route("/v1/chat/completions", post(completion)),
        )
        .await
        .unwrap();
    });
    let root = temp_dir("approval-root");
    let home = temp_dir("approval-home");
    std::fs::create_dir_all(&root).unwrap();
    let result = run_binary(&root, &home, address, "write note\ny\n/exit\n").await;
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(String::from_utf8_lossy(&result.stderr).contains("Allow write_file"));
    assert_eq!(
        std::fs::read_to_string(root.join("note.txt")).unwrap(),
        "hello"
    );
    std::fs::remove_dir_all(root).ok();
    std::fs::remove_dir_all(home).ok();
}

#[tokio::test]
async fn completed_tool_exchange_remains_valid_after_session_resume() {
    async fn completion(
        State(requests): State<Arc<Mutex<Vec<Value>>>>,
        Json(body): Json<Value>,
    ) -> Json<Value> {
        let mut requests = requests.lock().await;
        requests.push(body);
        let answer = if requests.len() == 1 {
            json!({"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call-1","type":"function","function":{"name":"write_file","arguments":"{\"path\":\"note.txt\",\"content\":\"hello\"}"}}]},"finish_reason":"tool_calls"}]})
        } else {
            json!({"choices":[{"message":{"role":"assistant","content":"written"},"finish_reason":"stop"}]})
        };
        Json(answer)
    }
    let requests = Arc::new(Mutex::new(Vec::new()));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let app = axum::Router::new()
        .route("/v1/chat/completions", post(completion))
        .with_state(Arc::clone(&requests));
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let root = temp_dir("resume-tool-root");
    let home = temp_dir("resume-tool-home");
    std::fs::create_dir_all(&root).unwrap();
    let first = run_binary(&root, &home, address, "write note\ny\n/exit\n").await;
    assert!(
        first.status.success(),
        "{}",
        String::from_utf8_lossy(&first.stderr)
    );
    let second = run_binary_with_args(
        &root,
        &home,
        address,
        "what changed?\n/exit\n",
        &["--continue"],
    )
    .await;
    assert!(
        second.status.success(),
        "{}",
        String::from_utf8_lossy(&second.stderr)
    );
    let requests = requests.lock().await;
    assert_eq!(requests.len(), 3);
    let messages = requests[2]["messages"].as_array().unwrap();
    let call = messages
        .iter()
        .position(|message| message["tool_calls"].is_array())
        .unwrap();
    assert_eq!(messages[call + 1]["role"], "tool");
    assert_eq!(messages[call + 1]["tool_call_id"], "call-1");
    assert_eq!(
        messages
            .iter()
            .filter(|message| message["content"] == "written")
            .count(),
        1
    );
    assert_eq!(
        std::fs::read_to_string(root.join("note.txt")).unwrap(),
        "hello"
    );
    std::fs::remove_dir_all(root).ok();
    std::fs::remove_dir_all(home).ok();
}

#[tokio::test]
async fn saved_auth_starts_chat_without_an_api_key_environment_variable() {
    async fn completion(Json(_body): Json<Value>) -> Json<Value> {
        Json(
            json!({"choices":[{"message":{"role":"assistant","content":"authenticated"},"finish_reason":"stop"}]}),
        )
    }
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(
            listener,
            axum::Router::new().route("/v1/chat/completions", post(completion)),
        )
        .await
        .unwrap();
    });
    let root = temp_dir("saved-auth-root");
    let home = temp_dir("saved-auth-home");
    std::fs::create_dir_all(&root).unwrap();
    save_key(&home, "saved-test-key").unwrap();
    let mut child = tokio::process::Command::new(env!("CARGO_BIN_EXE_structure"))
        .current_dir(&root)
        .env("STRUCTURE_HOME", &home)
        .env_remove("OPENAI__API_KEY")
        .env("OPENAI__BASE_URL", format!("http://{address}/v1"))
        .env("OPENAI__MODEL", "test-model")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(b"hello\n/exit\n")
        .await
        .unwrap();
    let result = tokio::time::timeout(Duration::from_secs(10), child.wait_with_output())
        .await
        .unwrap()
        .unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(String::from_utf8_lossy(&result.stdout).contains("authenticated"));
    assert!(!String::from_utf8_lossy(&result.stdout).contains("saved-test-key"));
    std::fs::remove_dir_all(root).ok();
    std::fs::remove_dir_all(home).ok();
}

/// The mock server's shared state: captured request bodies, plus the
/// workspace root so the first response can edit its `AGENTS.md`.
type Captured = Arc<Mutex<(Vec<Value>, Option<PathBuf>)>>;

#[tokio::test]
async fn agents_md_reaches_the_model_and_edits_apply_next_turn() {
    // Rewrites AGENTS.md while serving the first request, so the second
    // turn proves instructions are re-read per turn, not once at startup.
    async fn completion(State(state): State<Captured>, Json(body): Json<Value>) -> Json<Value> {
        let mut state = state.lock().await;
        if state.0.is_empty()
            && let Some(root) = &state.1
        {
            std::fs::write(
                root.join("AGENTS.md"),
                "Edited instruction: run the gold suite.\n",
            )
            .unwrap();
        }
        state.0.push(body);
        drop(state);
        Json(
            json!({"choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}),
        )
    }
    let state = Arc::new(Mutex::new((Vec::new(), None::<PathBuf>)));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let app = axum::Router::new()
        .route("/v1/chat/completions", post(completion))
        .with_state(Arc::clone(&state));
    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    let root = temp_dir("agents-md-root");
    let home = temp_dir("agents-md-home");
    std::fs::create_dir_all(&root).unwrap();
    std::fs::write(
        root.join("AGENTS.md"),
        "Original instruction: keep tests green.\n",
    )
    .unwrap();
    state.lock().await.1 = Some(root.clone());
    let result = run_binary(&root, &home, address, "first\nsecond\n/exit\n").await;
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let requests = std::mem::take(&mut state.lock().await.0);
    assert_eq!(requests.len(), 2);
    let first_system: Vec<&str> = system_message_contents(&requests[0]);
    assert!(
        first_system
            .iter()
            .any(|content| content.contains("Project instructions from AGENTS.md at AGENTS.md:")),
        "{first_system:?}"
    );
    assert!(
        first_system
            .iter()
            .any(|content| content.contains("Original instruction: keep tests green.")),
        "{first_system:?}"
    );
    let second_system: Vec<&str> = system_message_contents(&requests[1]);
    assert!(
        second_system
            .iter()
            .any(|content| content.contains("Edited instruction: run the gold suite.")),
        "{second_system:?}"
    );
    assert!(
        !second_system
            .iter()
            .any(|content| content.contains("Original instruction: keep tests green.")),
        "{second_system:?}"
    );
    // The session header records a hash of the instructions in effect at
    // creation time, not a second copy of the prompt text.
    let header = session_file_header(&home);
    let hash = header["instructions_sha256"].as_str().unwrap();
    assert_eq!(hash.len(), 64, "{hash}");
    assert!(hash.chars().all(|c| c.is_ascii_hexdigit()), "{hash}");
    std::fs::remove_dir_all(root).ok();
    std::fs::remove_dir_all(home).ok();
}

fn system_message_contents(request: &Value) -> Vec<&str> {
    request["messages"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|message| message["role"] == "system")
        .filter_map(|message| message["content"].as_str())
        .collect()
}

/// Reads the single session file's header line under `home`, the way
/// `structure sessions list` does.
fn session_file_header(home: &std::path::Path) -> Value {
    fn walk(dir: &std::path::Path, found: &mut Vec<PathBuf>) {
        for entry in std::fs::read_dir(dir).unwrap().flatten() {
            let path = entry.path();
            if path.is_dir() {
                walk(&path, found);
            } else if path
                .extension()
                .is_some_and(|extension| extension == "jsonl")
            {
                found.push(path);
            }
        }
    }
    let mut found = Vec::new();
    walk(home, &mut found);
    assert_eq!(found.len(), 1, "expected exactly one session file");
    let first_line = std::fs::read_to_string(&found[0])
        .unwrap()
        .lines()
        .next()
        .unwrap()
        .to_owned();
    serde_json::from_str(&first_line).unwrap()
}
