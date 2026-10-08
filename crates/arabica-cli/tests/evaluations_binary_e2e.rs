//! Model-free, read-only evaluation queries for CLI and desktop wrappers.
use std::process::Command;

#[test]
fn evaluation_json_query_needs_no_model_or_database_and_creates_no_files() {
    let home =
        std::env::temp_dir().join(format!("arabica-evaluation-query-{}", uuid::Uuid::now_v7()));
    std::fs::create_dir_all(&home).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_arabica"))
        .args([
            "evaluations",
            "show",
            "missing",
            "--workspace-id",
            "workspace",
            "--json",
        ])
        .env("ARABICA_HOME", &home)
        .env_remove("OPENAI__API_KEY")
        .env_remove("OPENAI__BASE_URL")
        .env_remove("OPENAI__MODEL")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let view: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(view["schema_version"], 1);
    assert_eq!(view["session_id"], "missing");
    assert!(view["report"].is_null());
    assert_eq!(std::fs::read_dir(&home).unwrap().count(), 0);
    let output = Command::new(env!("CARGO_BIN_EXE_arabica"))
        .args(["-p", "task", "evaluations", "show", "missing"])
        .env("ARABICA_HOME", &home)
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(2));
    std::fs::remove_dir_all(home).unwrap();
}
