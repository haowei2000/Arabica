use std::io::Write;

use agent_client_protocol::schema::v1::SessionConfigKind;

use super::*;

const CONFIG: &str = r#"
[providers.test]
api_type = "open_ai_responses"
api_key = "test-key"
base_url = "http://127.0.0.1:9/v1"
thinking = "high"

[models.fast]
provider = "test"
model_id = "model-fast"

[models.strong]
provider = "test"
model_id = "model-strong"

[blend]
default_policy = "routed"

[blend.policies.routed]
default_model = "fast"
after_tool_success = "strong"
after_tool_error = "strong"
recovery_model = "strong"
planning_model = "strong"
tool_call_capable_models = ["fast", "strong"]
typed_completion_capable_models = ["strong"]

[[blend.policies.routed.tool_routes]]
id = "shell"
matcher = { type = "exact_name", value = "shell" }
model = "strong"

[blend.policies.other]
default_model = "strong"
"#;

struct Fixture {
    root: PathBuf,
    home: PathBuf,
    cwd: PathBuf,
}

impl Fixture {
    fn new(config: &str) -> Self {
        let root = std::env::temp_dir().join(format!("arabica-acp-soe-{}", uuid::Uuid::now_v7()));
        let home = root.join("home");
        let cwd = root.join("project");
        std::fs::create_dir_all(&home).unwrap();
        std::fs::create_dir_all(&cwd).unwrap();
        let fixture = Self { root, home, cwd };
        fixture.write(config);
        fixture
    }

    fn write(&self, config: &str) {
        let mut options = std::fs::OpenOptions::new();
        options.create(true).truncate(true).write(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        options
            .open(self.home.join("config.toml"))
            .unwrap()
            .write_all(config.as_bytes())
            .unwrap();
    }

    fn resolved(&self) -> ResolvedCliConfig {
        crate::config::resolve_cli_acp_config(&Default::default(), &self.home, &self.cwd, |_| None)
            .unwrap()
    }

    fn settings(&self) -> AcpProviderSettings {
        AcpProviderSettings::from_resolved(Default::default(), &self.resolved()).unwrap()
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        std::fs::remove_dir_all(&self.root).ok();
    }
}

fn selected_value(options: &[SessionConfigOption], id: &str) -> String {
    let option = options
        .iter()
        .find(|option| option.id.to_string() == id)
        .unwrap();
    let SessionConfigKind::Select(select) = &option.kind else {
        panic!("expected a select option");
    };
    select.current_value.to_string()
}

#[test]
fn soe_clears_every_named_route_and_declared_capability() {
    let fixture = Fixture::new(CONFIG);
    let settings = fixture.settings();
    let configured = settings.blend_policy.as_ref().unwrap();
    assert!(configured.planning_model.is_some());
    assert!(!configured.tool_routes.is_empty());
    assert!(!configured.tool_call_capable_models.is_empty());
    assert!(!configured.typed_completion_capable_models.is_empty());
    crate::config::save_workspace_settings(&fixture.home, &fixture.cwd, |preferences| {
        preferences.blend_policy = Some(SOE_POLICY_ID.to_owned());
        preferences.model_alias = Some("strong".to_owned());
        preferences
            .model_thinking
            .insert("strong".to_owned(), "max".to_owned());
    })
    .unwrap();
    let soe = settings.for_workspace(&fixture.home, &fixture.cwd).unwrap();
    let policy = soe.blend_policy.as_ref().unwrap();
    assert_eq!(policy.policy_id, SOE_POLICY_ID);
    assert_eq!(policy.default_model, "strong");
    assert!(policy.after_tool_success.is_none());
    assert!(policy.after_tool_error.is_none());
    assert!(policy.recovery_model.is_none());
    assert!(policy.planning_model.is_none());
    assert!(policy.tool_routes.is_empty());
    assert_eq!(
        policy.tool_call_capable_models,
        ["strong".to_owned()].into()
    );
    assert_eq!(
        policy.typed_completion_capable_models,
        ["strong".to_owned()].into()
    );
    assert_eq!(crate::config::thinking_level(&soe.initial), "max");
    let options = soe.options(&soe.initial, Some(policy));
    assert_eq!(selected_value(&options, "blend.policy"), SOE_POLICY_ID);
    assert_eq!(selected_value(&options, "model"), "strong");
    assert_eq!(selected_value(&options, "thinking"), "max");
}

#[tokio::test]
async fn soe_prompt_with_tools_reaches_only_the_selected_model() {
    use std::sync::atomic::{AtomicUsize, Ordering};

    use axum::Json;
    use axum::routing::post;
    use axum::{Router, serve};

    let calls = Arc::new(AtomicUsize::new(0));
    let received_calls = Arc::clone(&calls);
    let app = Router::new().route("/v1/chat/completions", post(move |Json(body): Json<serde_json::Value>| {
        let received_calls = Arc::clone(&received_calls);
        async move {
            assert_eq!(body["model"], "model-strong");
            assert!(!body["tools"].as_array().unwrap().is_empty());
            assert_eq!(body["thinking"]["type"], "enabled");
            received_calls.fetch_add(1, Ordering::SeqCst);
            Json(serde_json::json!({
                "choices": [{"message": {"role": "assistant", "content": "SOE answer"}, "finish_reason": "stop"}]
            }))
        }
    }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move { serve(listener, app).await.unwrap() });
    let config = CONFIG
        .replace("http://127.0.0.1:9/v1", &format!("http://{address}/v1"))
        .replace("open_ai_responses", "open_ai_chat_completions")
        .replace("thinking = \"high\"", "thinking = \"off\"");
    let fixture = Fixture::new(&config);
    let settings = fixture.settings();
    let factory_settings = settings.clone();
    let state = AcpState::new(
        Arc::new(move || factory_settings.build_model()),
        LocalRunnerPolicy::coding(),
        fixture.home.clone(),
    )
    .with_provider_settings(settings);
    let session = state
        .new_session(NewSessionRequest::new(fixture.cwd.clone()), None)
        .await
        .unwrap();
    for (option, value) in [
        ("blend.policy", SOE_POLICY_ID),
        ("model", "strong"),
        ("thinking", "on"),
    ] {
        state
            .set_config_option(SetSessionConfigOptionRequest::new(
                session.session_id.clone(),
                option,
                value,
            ))
            .unwrap();
    }
    let entry = state.entry(&session.session_id).unwrap();
    let mut manager = entry.manager.lock().await;
    let events = manager
        .dispatch(
            CommandEnvelope::new(
                CommandId::new(uuid::Uuid::now_v7().to_string()),
                Some(entry.arabica_session_id.clone()),
                Command::MessageSend {
                    images: Vec::new(),
                    content: "Answer briefly.".to_owned(),
                },
            ),
            DispatchControl::default(),
        )
        .await
        .unwrap();
    assert!(
        calls.load(Ordering::SeqCst) > 0,
        "SOE must reach the provider with tools enabled"
    );
    assert!(
        events
            .iter()
            .any(|event| matches!(event.event, arabica_protocol::Event::RunCompleted { .. }))
    );
    assert!(
        !events
            .iter()
            .any(|event| matches!(event.event, arabica_protocol::Event::RunFailed { .. }))
    );
    for event in &events {
        if let arabica_protocol::Event::ModelRouteSelected { model_alias, .. } = &event.event {
            assert_eq!(model_alias.as_deref(), Some("strong"));
        }
    }
    server.abort();
}

#[tokio::test]
async fn selecting_named_policy_reloads_file_thinking_and_preserves_soe_preferences() {
    let fixture = Fixture::new(CONFIG);
    let settings = fixture.settings();
    let factory_settings = settings.clone();
    let state = AcpState::new(
        Arc::new(move || factory_settings.build_model()),
        LocalRunnerPolicy::coding(),
        fixture.home.clone(),
    )
    .with_provider_settings(settings);
    let session = state
        .new_session(NewSessionRequest::new(fixture.cwd.clone()), None)
        .await
        .unwrap();
    state
        .set_config_option(SetSessionConfigOptionRequest::new(
            session.session_id.clone(),
            "blend.policy",
            SOE_POLICY_ID,
        ))
        .unwrap();
    state
        .set_config_option(SetSessionConfigOptionRequest::new(
            session.session_id.clone(),
            "thinking",
            "low",
        ))
        .unwrap();
    fixture.write(&CONFIG.replace("thinking = \"high\"", "thinking = \"medium\""));
    let changed = state
        .set_config_option(SetSessionConfigOptionRequest::new(
            session.session_id.clone(),
            "blend.policy",
            "other",
        ))
        .unwrap();
    assert_eq!(changed.config_options.len(), 1);
    assert_eq!(
        selected_value(&changed.config_options, "blend.policy"),
        "other"
    );
    let entry = state.entry(&session.session_id).unwrap();
    let configs = entry.model_configs.as_ref().unwrap().lock().unwrap();
    assert!(
        configs
            .values()
            .all(|config| crate::config::thinking_level(config) == "medium")
    );
    drop(configs);
    let saved = crate::config::read_workspace_settings(&fixture.home, &fixture.cwd).unwrap();
    assert_eq!(saved.model_alias.as_deref(), Some("fast"));
    assert_eq!(saved.model_thinking["fast"], "low");
    for (option, value) in [("model", "fast"), ("thinking", "high")] {
        assert!(
            state
                .set_config_option(SetSessionConfigOptionRequest::new(
                    session.session_id.clone(),
                    option,
                    value,
                ))
                .is_err()
        );
    }
    let after_rejection =
        crate::config::read_workspace_settings(&fixture.home, &fixture.cwd).unwrap();
    assert_eq!(after_rejection.blend_policy.as_deref(), Some("other"));
    assert_eq!(after_rejection.model_thinking["fast"], "low");
    let restored = state
        .set_config_option(SetSessionConfigOptionRequest::new(
            session.session_id.clone(),
            "blend.policy",
            SOE_POLICY_ID,
        ))
        .unwrap();
    assert_eq!(selected_value(&restored.config_options, "model"), "fast");
    assert_eq!(selected_value(&restored.config_options, "thinking"), "low");
}

#[test]
fn cli_config_policy_keeps_its_routes_and_still_offers_soe() {
    let config = CONFIG.replace(
        "[blend]\ndefault_policy = \"routed\"\n\n[blend.policies.routed]",
        "[blend]",
    );
    let config = config
        .split("[blend.policies.other]")
        .next()
        .unwrap()
        .replace(
            "[[blend.policies.routed.tool_routes]]",
            "[[blend.tool_routes]]",
        );
    let fixture = Fixture::new(&config);
    let settings = fixture
        .settings()
        .for_workspace(&fixture.home, &fixture.cwd)
        .unwrap();
    let policy = settings.blend_policy.as_ref().unwrap();
    assert_eq!(policy.policy_id, "cli-config");
    assert_eq!(policy.recovery_model.as_deref(), Some("strong"));
    assert!(!policy.tool_routes.is_empty());
    let options = settings.options(&settings.initial, Some(policy));
    assert_eq!(options.len(), 1);
    assert_eq!(selected_value(&options, "blend.policy"), "cli-config");
    let SessionConfigKind::Select(select) = &options[0].kind else {
        unreachable!()
    };
    let encoded = serde_json::to_value(select).unwrap();
    assert!(
        encoded["options"]
            .as_array()
            .unwrap()
            .iter()
            .any(|option| option["value"] == SOE_POLICY_ID && option["name"] == "SOE")
    );
}

#[test]
fn configured_soe_policy_is_rejected_instead_of_hijacking_its_routes() {
    let fixture = Fixture::new(&CONFIG.replace("routed", SOE_POLICY_ID));
    let error = AcpProviderSettings::from_resolved(Default::default(), &fixture.resolved())
        .err()
        .expect("configured soe key must be rejected");
    assert!(error.to_string().contains("soe is reserved"));
    assert!(error.to_string().contains("rename the configured policy"));
}
