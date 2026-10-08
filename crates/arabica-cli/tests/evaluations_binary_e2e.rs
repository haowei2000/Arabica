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

#[test]
fn evaluations_policies_command_reads_empty_database_and_persisted_candidates() {
    let home = std::env::temp_dir().join(format!(
        "arabica-evaluation-policies-{}",
        uuid::Uuid::now_v7()
    ));
    std::fs::create_dir_all(&home).unwrap();

    // Query on empty directory
    let output = Command::new(env!("CARGO_BIN_EXE_arabica"))
        .args(["evaluations", "policies", "--json"])
        .env("ARABICA_HOME", &home)
        .output()
        .unwrap();
    assert!(output.status.success());
    let list: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(list, serde_json::json!([]));

    // Seed a candidate policy in evaluation.sqlite3
    let db_path = home.join("evaluation.sqlite3");
    let mut db = arabica_adapters::SqliteEvaluationStore::open(&db_path).unwrap();
    let policy = arabica_runtime::BlendRoutingPolicy {
        policy_id: "test-policy".to_string(),
        version: 2,
        default_model: "fast".to_string(),
        after_tool_success: None,
        after_tool_error: Some("strong".to_string()),
        recovery_model: None,
        planning_model: None,
        tool_routes: vec![],
        recovery_after_no_progress_steps: 2,
        minimum_model_dwell_steps: 1,
        tool_call_capable_models: std::collections::BTreeSet::new(),
        typed_completion_capable_models: std::collections::BTreeSet::new(),
    };
    db.save_policy_version(&arabica_runtime::GeneratedPolicyRecord {
        policy_id: "test-policy".to_string(),
        version: 2,
        status: "candidate".to_string(),
        policy,
        basis_fingerprint: Some("fp-test".to_string()),
        reason: "evolved candidate for testing".to_string(),
    })
    .unwrap();
    drop(db);

    // Query with --json
    let output = Command::new(env!("CARGO_BIN_EXE_arabica"))
        .args(["evaluations", "policies", "--json"])
        .env("ARABICA_HOME", &home)
        .output()
        .unwrap();
    assert!(output.status.success());
    let list: Vec<arabica_runtime::GeneratedPolicyRecord> =
        serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(list.len(), 1);
    assert_eq!(list[0].policy_id, "test-policy");
    assert_eq!(list[0].version, 2);
    assert_eq!(list[0].status, "candidate");

    // Query human-readable
    let output = Command::new(env!("CARGO_BIN_EXE_arabica"))
        .args(["evaluations", "policies"])
        .env("ARABICA_HOME", &home)
        .output()
        .unwrap();
    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("Policy: test-policy v2 [candidate]"));

    std::fs::remove_dir_all(home).unwrap();
}
