//! The structured settings commands must work before provider resolution.

use std::io::Write;
use std::path::Path;
use std::process::{Command, Output, Stdio};

use serde_json::{Value, json};

fn invoke(home: &Path, arguments: &[&str], input: Option<&Value>) -> Output {
    let mut child = Command::new(env!("CARGO_BIN_EXE_arabica"))
        .args(arguments)
        .env_clear()
        .env("ARABICA_HOME", home)
        .current_dir(home)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("start settings CLI");
    if let Some(input) = input {
        child
            .stdin
            .take()
            .unwrap()
            .write_all(&serde_json::to_vec(input).unwrap())
            .unwrap();
    }
    child.wait_with_output().expect("settings CLI exits")
}

#[test]
fn inspect_and_apply_require_no_working_provider_and_receive_secrets_only_on_stdin() {
    let home = tempfile::tempdir().unwrap();
    let inspected = invoke(home.path(), &["config", "inspect"], None);
    assert!(inspected.status.success());
    assert!(inspected.stderr.is_empty());
    let snapshot: Value = serde_json::from_slice(&inspected.stdout).unwrap();
    assert_eq!(snapshot["document"], json!({}));
    let input = json!({
        "expected_revision": snapshot["revision"],
        "edits": [{"op":"set", "path":["providers","new"], "value":{
            "api_type":"open_ai_responses", "api_key":"stdin-fixture-secret",
            "base_url":"https://example.invalid/v1"
        }}]
    });
    let applied = invoke(home.path(), &["config", "apply"], Some(&input));
    assert!(
        applied.status.success(),
        "{}",
        String::from_utf8_lossy(&applied.stdout)
    );
    assert!(applied.stderr.is_empty());
    let output = String::from_utf8(applied.stdout).unwrap();
    assert!(!output.contains("stdin-fixture-secret"));
    let snapshot: Value = serde_json::from_str(&output).unwrap();
    assert_eq!(
        snapshot["document"]["providers"]["new"]["api_key"],
        Value::Null
    );
    assert_eq!(
        snapshot["secret_paths"],
        json!([["providers", "new", "api_key"]])
    );
}

#[test]
fn bare_config_keeps_its_resolved_configuration_description() {
    let home = tempfile::tempdir().unwrap();
    std::fs::write(
        home.path().join("config.toml"),
        r#"
[providers.fixture]
api_key_env = "FIXTURE_KEY"
base_url = "https://example.invalid/v1"
[models.default]
provider = "fixture"
model_id = "fixture-model"
[blend]
default_model = "default"
"#,
    )
    .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_arabica"))
        .arg("config")
        .env_clear()
        .env("ARABICA_HOME", home.path())
        .env("FIXTURE_KEY", "fixture-key")
        .current_dir(home.path())
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let text = String::from_utf8(output.stdout).unwrap();
    assert!(text.contains("configuration resolved"));
    assert!(text.contains("fixture-model"));
    assert!(!text.contains("fixture-key"));
    assert!(serde_json::from_str::<Value>(&text).is_err());
}

#[test]
fn failed_apply_returns_safe_json_without_writing() {
    let home = tempfile::tempdir().unwrap();
    let inspected = invoke(home.path(), &["config", "inspect"], None);
    let snapshot: Value = serde_json::from_slice(&inspected.stdout).unwrap();
    let input = json!({"expected_revision":snapshot["revision"], "edits":[
        {"op":"set", "path":["providers","new","base_url"], "value":"private-invalid-value"}
    ]});
    let output = invoke(home.path(), &["config", "apply"], Some(&input));
    assert!(!output.status.success());
    let text = String::from_utf8(output.stdout).unwrap();
    assert!(!text.contains("private-invalid-value"));
    let error: Value = serde_json::from_str(&text).unwrap();
    assert_eq!(error["error"]["code"], "validation");
    assert_eq!(
        error["error"]["path"],
        json!(["providers", "new", "base_url"])
    );
    assert!(!home.path().join("config.toml").exists());
}
