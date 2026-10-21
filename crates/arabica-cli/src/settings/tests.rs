use super::*;
use serde_json::json;

fn request(snapshot: &SettingsSnapshot, edits: Json) -> ApplyRequest {
    serde_json::from_value(json!({
        "expected_revision": snapshot.revision,
        "edits": edits,
    }))
    .expect("valid edit fixture")
}

fn save(home: &Path, edits: Json) -> Result<SettingsSnapshot, SettingsError> {
    let snapshot = inspect(home)?;
    apply(home, request(&snapshot, edits))
}

#[test]
fn missing_configuration_inspects_as_an_empty_document() {
    let directory = tempfile::tempdir().unwrap();
    let snapshot = inspect(directory.path()).unwrap();
    assert_eq!(snapshot.document, json!({}));
    assert!(snapshot.secret_paths.is_empty());
    assert_eq!(snapshot.revision, revision(""));
    assert!(!crate::config::user_config_path(directory.path()).exists());
}

#[test]
fn independent_provider_model_policy_mcp_and_skills_edits_need_no_live_credentials() {
    let directory = tempfile::tempdir().unwrap();
    let home = directory.path();
    let provider = save(home, json!([
        {"op":"set", "path":["providers","0"], "value":{
            "api_type":"open_ai_responses", "base_url":"https://example.invalid/v1", "thinking":"low"
        }}
    ])).unwrap();
    assert_eq!(provider.document["providers"]["0"]["thinking"], "low");
    assert!(provider.document.get("models").is_none());
    let model = save(home, json!([
        {"op":"set", "path":["models","quoted.alias"], "value":{"provider":"0", "model_id":"not-connected"}}
    ])).unwrap();
    assert_eq!(model.document["models"]["quoted.alias"]["provider"], "0");
    let snapshot = save(home, json!([
        {"op":"set", "path":["blend","default_policy"], "value":"daily"},
        {"op":"set", "path":["blend","policies","daily"], "value":{"default_model":"quoted.alias", "minimum_model_dwell_steps":1}},
        {"op":"append", "path":["mcp"], "value":{"name":"local", "command":"/does/not/need/to/exist", "args":["--fixture"], "env":{"TOKEN":"private-env"}}},
        {"op":"append", "path":["mcp"], "value":{"name":"remote", "url":"https://example.invalid/mcp", "headers":{"Authorization":"Bearer private-header"}}},
        {"op":"append", "path":["skills","roots"], "value":"local-skills"}
    ])).unwrap();
    assert_eq!(
        snapshot.document["blend"]["policies"]["daily"]["default_model"],
        "quoted.alias"
    );
    assert_eq!(snapshot.document["mcp"].as_array().unwrap().len(), 2);
    assert_eq!(
        snapshot.document["skills"]["roots"],
        json!(["local-skills"])
    );
    assert_eq!(snapshot.document["mcp"][0]["env"]["TOKEN"], Json::Null);
    assert_eq!(
        snapshot.document["mcp"][1]["headers"]["Authorization"],
        Json::Null
    );
    assert!(snapshot.secret_paths.contains(&vec![
        "mcp".into(),
        "0".into(),
        "env".into(),
        "TOKEN".into()
    ]));
}

#[test]
fn scalar_edits_preserve_comments_quoted_keys_advanced_tables_and_secrets() {
    let directory = tempfile::tempdir().unwrap();
    let home = directory.path();
    let source = r#"# Personal configuration
[providers."provider.with.dot"]
api_type = "open_ai_responses"
base_url = "https://example.invalid/v1" # keep endpoint comment
api_key = "private-provider-key" # keep secret comment
thinking = "low"

[models."0"]
provider = "provider.with.dot"
model_id = "model-original"

[blend]
default_model = "0"
tool_routes = [{ id = "route", matcher = { type = "exact_name", value = "read_file" }, model = "0" }]

[skills]
roots = ["first", "second"] # keep roots comment

[evaluation]
max_clusters = 2
min_cluster_runs = 4
"#;
    fs::write(crate::config::user_config_path(home), source).unwrap();
    let before = inspect(home).unwrap();
    assert_eq!(
        before.document["providers"]["provider.with.dot"]["api_key"],
        Json::Null
    );
    assert!(
        !serde_json::to_string(&before)
            .unwrap()
            .contains("private-provider-key")
    );
    let after = apply(home, request(&before, json!([
        {"op":"set", "path":["models","0","model_id"], "value":"model-updated"},
        {"op":"set", "path":["providers","provider.with.dot","base_url"], "value":"https://changed.invalid/v1"},
        {"op":"remove", "path":["skills","roots","0"]},
        {"op":"append", "path":["skills","roots"], "value":"third"}
    ]))).unwrap();
    let written = fs::read_to_string(crate::config::user_config_path(home)).unwrap();
    assert!(written.contains("# Personal configuration"));
    assert!(written.contains("# keep endpoint comment"));
    assert!(written.contains("api_key = \"private-provider-key\" # keep secret comment"));
    assert!(written.contains("# keep roots comment"));
    assert!(written.contains("[evaluation]\nmax_clusters = 2\nmin_cluster_runs = 4"));
    assert!(written.contains("tool_routes = [{ id = \"route\", matcher = { type = \"exact_name\", value = \"read_file\" }, model = \"0\" }]"));
    assert_eq!(
        after.document["skills"]["roots"],
        json!(["second", "third"])
    );
    assert_eq!(after.document["models"]["0"]["model_id"], "model-updated");
    assert_ne!(before.revision, after.revision);
}

#[test]
fn nested_arrays_are_edited_in_order_and_numeric_table_ids_stay_keys() {
    let directory = tempfile::tempdir().unwrap();
    let home = directory.path();
    let after = save(home, json!([
        {"op":"set", "path":["providers","123"], "value":{"base_url":"https://example.invalid"}},
        {"op":"append", "path":["mcp"], "value":{"name":"fixture", "command":"/fixture", "args":["one","two","three"]}},
        {"op":"set", "path":["mcp","0","args","1"], "value":"changed"},
        {"op":"remove", "path":["mcp","0","args","0"]},
        {"op":"append", "path":["mcp","0","args"], "value":"last"},
        {"op":"set", "path":["mcp","0","env","TOKEN"], "value":"private-token"}
    ])).unwrap();
    assert_eq!(
        after.document["mcp"][0]["args"],
        json!(["changed", "three", "last"])
    );
    assert_eq!(
        after.document["providers"]["123"]["base_url"],
        "https://example.invalid"
    );
    assert_eq!(after.document["mcp"][0]["env"]["TOKEN"], Json::Null);
}

#[test]
fn conflicts_and_invalid_requests_never_replace_configuration() {
    let directory = tempfile::tempdir().unwrap();
    let home = directory.path();
    save(home, json!([{"op":"set", "path":["providers","test"], "value":{"base_url":"https://example.invalid", "api_key":"keep-private"}}])).unwrap();
    let before = inspect(home).unwrap();
    let path = crate::config::user_config_path(home);
    let original = fs::read_to_string(&path).unwrap();
    let mut conflict = request(
        &before,
        json!([{"op":"set", "path":["providers","test","thinking"], "value":"on"}]),
    );
    conflict.expected_revision = revision("another file");
    assert_eq!(apply(home, conflict).unwrap_err().code, "conflict");
    for edits in [
        json!([{"op":"set", "path":["providers","test","api_key"], "value":null}]),
        json!([{"op":"remove", "path":[]}]),
        json!([{"op":"set", "path":["mcp","9","name"], "value":"outside"}]),
        json!([{"op":"set", "path":["providers","test","base_url"], "value":"private-invalid-url"}]),
        json!([{"op":"set", "path":["models","bad"], "value":{"provider":"missing", "model_id":"id"}}]),
        json!([{"op":"append", "path":["skills","roots"], "value":""}]),
    ] {
        let error = apply(home, request(&before, edits)).unwrap_err();
        assert!(
            !serde_json::to_string(&error)
                .unwrap()
                .contains("private-invalid-url")
        );
        assert!(
            !serde_json::to_string(&error)
                .unwrap()
                .contains("keep-private")
        );
        assert_eq!(fs::read_to_string(&path).unwrap(), original);
    }
    let error = apply(
        home,
        request(
            &before,
            json!([
                {"op":"set", "path":["providers","test","base_url"], "value":"not-a-url"}
            ]),
        ),
    )
    .unwrap_err();
    assert_eq!(error.path, ["providers", "test", "base_url"]);
}

#[test]
fn deleting_a_referenced_provider_is_rejected_without_writing() {
    let directory = tempfile::tempdir().unwrap();
    let home = directory.path();
    let before = save(
        home,
        json!([
            {"op":"set", "path":["providers","test"], "value":{}},
            {"op":"set", "path":["models","model"], "value":{"provider":"test", "model_id":"model"}}
        ]),
    )
    .unwrap();
    let error = apply(
        home,
        request(
            &before,
            json!([{"op":"remove", "path":["providers","test"]}]),
        ),
    )
    .unwrap_err();
    assert_eq!(error.path, ["models", "model", "provider"]);
    assert_eq!(inspect(home).unwrap().revision, before.revision);
}

#[test]
fn unknown_and_legacy_fields_are_preserved_and_safely_rejected_on_apply() {
    for source in [
        "[provider]\napi_key='legacy-private-key'\nbase_url='https://example.invalid'\n",
        "[unrecognized]\nprivate_key='advanced-private-key'\nvalue=42\n",
    ] {
        let directory = tempfile::tempdir().unwrap();
        let home = directory.path();
        let path = crate::config::user_config_path(home);
        fs::write(&path, source).unwrap();
        let snapshot = inspect(home).unwrap();
        assert!(
            !serde_json::to_string(&snapshot)
                .unwrap()
                .contains("private-key")
        );
        let error = apply(
            home,
            request(
                &snapshot,
                json!([
                    {"op":"append", "path":["skills","roots"], "value":"skills"}
                ]),
            ),
        )
        .unwrap_err();
        assert_eq!(error.code, "validation");
        assert_eq!(fs::read_to_string(&path).unwrap(), source);
    }
}

#[test]
fn malformed_mcp_tables_redact_credential_values_without_hiding_other_fields() {
    let directory = tempfile::tempdir().unwrap();
    let source = r#"
[mcp]
name = "repairable-server"
command = "/fixture"
note = "keep-visible"
[mcp.env]
CUSTOM_CREDENTIAL = "private-malformed-environment"
NESTED = { arbitrary = "private-malformed-nested-value" }
[mcp.headers]
X-Custom = "private-malformed-header"
"#;
    fs::write(crate::config::user_config_path(directory.path()), source).unwrap();
    let snapshot = inspect(directory.path()).unwrap();
    assert_eq!(snapshot.document["mcp"]["note"], "keep-visible");
    assert_eq!(snapshot.document["mcp"]["command"], "/fixture");
    assert_eq!(
        snapshot.document["mcp"]["env"],
        json!({"CUSTOM_CREDENTIAL": null, "NESTED": null})
    );
    assert_eq!(
        snapshot.document["mcp"]["headers"],
        json!({"X-Custom": null})
    );
    assert!(snapshot.secret_paths.contains(&vec![
        "mcp".into(),
        "env".into(),
        "CUSTOM_CREDENTIAL".into()
    ]));
    assert!(snapshot.secret_paths.contains(&vec![
        "mcp".into(),
        "headers".into(),
        "X-Custom".into()
    ]));
    assert!(
        !serde_json::to_string(&snapshot)
            .unwrap()
            .contains("private-malformed")
    );
}

#[test]
fn malformed_mcp_scalar_and_array_credentials_are_redacted_as_whole_containers() {
    for source in [
        "[[mcp]]\nname='fixture'\ncommand='/fixture'\nenv='private-scalar-env'\nheaders=['private-array-header']\n",
        "[[mcp]]\nname='fixture'\ncommand='/fixture'\nenv=['private-array-env']\nheaders='private-scalar-header'\n",
        "[mcp]\nname='fixture'\nenv='private-table-env'\nheaders=['private-table-header']\n",
        "[mcp.nested]\nnote='keep-visible'\nenv=['private-nested-env']\nheaders='private-nested-header'\n",
    ] {
        let directory = tempfile::tempdir().unwrap();
        fs::write(crate::config::user_config_path(directory.path()), source).unwrap();
        let snapshot = inspect(directory.path()).unwrap();
        let container = if snapshot.document["mcp"].is_array() {
            &snapshot.document["mcp"][0]
        } else if snapshot.document["mcp"].get("nested").is_some() {
            &snapshot.document["mcp"]["nested"]
        } else {
            &snapshot.document["mcp"]
        };
        assert_eq!(container["env"], Json::Null);
        assert_eq!(container["headers"], Json::Null);
        assert_eq!(snapshot.secret_paths.len(), 2);
        assert!(
            !serde_json::to_string(&snapshot)
                .unwrap()
                .contains("private-")
        );
    }
}

#[test]
fn provider_credential_keys_remain_redacted_with_malformed_value_shapes() {
    let directory = tempfile::tempdir().unwrap();
    fs::write(
        crate::config::user_config_path(directory.path()),
        "[providers.fixture]\nbase_url='https://example.invalid'\napi_key=['private-array-key']\nclient_secret={ value='private-nested-client-secret' }\n",
    ).unwrap();
    let snapshot = inspect(directory.path()).unwrap();
    assert_eq!(
        snapshot.document["providers"]["fixture"]["api_key"],
        Json::Null
    );
    assert_eq!(
        snapshot.document["providers"]["fixture"]["client_secret"],
        Json::Null
    );
    assert_eq!(
        snapshot.document["providers"]["fixture"]["base_url"],
        "https://example.invalid"
    );
    assert!(
        !serde_json::to_string(&snapshot)
            .unwrap()
            .contains("private-")
    );
}

#[test]
fn concurrent_forms_cannot_silently_overwrite_each_other() {
    let directory = tempfile::tempdir().unwrap();
    let revision = inspect(directory.path()).unwrap().revision;
    let handles: Vec<_> = ["first", "second"]
        .into_iter()
        .map(|name| {
            let home = directory.path().to_owned();
            let revision = revision.clone();
            std::thread::spawn(move || {
                apply(
                    &home,
                    ApplyRequest {
                        expected_revision: revision,
                        edits: vec![Edit::Append {
                            path: vec!["skills".into(), "roots".into()],
                            value: json!(name),
                        }],
                    },
                )
            })
        })
        .collect();
    let results: Vec<_> = handles
        .into_iter()
        .map(|handle| handle.join().unwrap())
        .collect();
    assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
    assert_eq!(
        results.into_iter().find_map(Result::err).unwrap().code,
        "conflict"
    );
    assert_eq!(
        inspect(directory.path()).unwrap().document["skills"]["roots"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
}

#[cfg(unix)]
#[test]
fn writes_are_owner_only_and_symbolic_links_are_never_followed() {
    use std::os::unix::fs::{PermissionsExt, symlink};
    let directory = tempfile::tempdir().unwrap();
    let home = directory.path();
    let snapshot = save(
        home,
        json!([{"op":"append", "path":["skills","roots"], "value":"one"}]),
    )
    .unwrap();
    let path = crate::config::user_config_path(home);
    assert_eq!(
        fs::metadata(&path).unwrap().permissions().mode() & 0o777,
        0o600
    );
    fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
    apply(
        home,
        request(
            &snapshot,
            json!([{"op":"append", "path":["skills","roots"], "value":"two"}]),
        ),
    )
    .unwrap();
    assert_eq!(
        fs::metadata(&path).unwrap().permissions().mode() & 0o777,
        0o600
    );

    let linked = tempfile::tempdir().unwrap();
    symlink(&path, crate::config::user_config_path(linked.path())).unwrap();
    assert_eq!(inspect(linked.path()).unwrap_err().code, "io");
    assert_eq!(
        apply(
            linked.path(),
            ApplyRequest {
                expected_revision: revision(""),
                edits: vec![]
            }
        )
        .unwrap_err()
        .code,
        "io"
    );
    let locked = tempfile::tempdir().unwrap();
    symlink(&path, locked.path().join("config.toml.lock")).unwrap();
    assert_eq!(
        apply(
            locked.path(),
            ApplyRequest {
                expected_revision: revision(""),
                edits: vec![]
            }
        )
        .unwrap_err()
        .code,
        "io"
    );
    assert_eq!(
        inspect(home).unwrap().document["skills"]["roots"],
        json!(["one", "two"])
    );
}
