//! HTTP + SSE binding for the canonical Structure protocol.

use std::collections::BTreeMap;
use std::convert::Infallible;
use std::path::PathBuf;
use std::sync::Arc;

use arabica_protocol::{CommandEnvelope, CommandFailure, EventEnvelope, protocol_schema};
use arabica_provider::{
    ApiProviderConfig, ApiType, BlendProvider, EchoModel, ModelProvider, ModelRunRequest,
    ModelRunResult, ProviderError,
};
use arabica_runner::LocalRunner;
use arabica_runtime::{
    BlendRoutingPolicy, CoreRuntime, RuntimeArchiveStore, RuntimeCompactionStrategy,
    ShortMemoryPolicy,
};
use arabica_session::SessionManager;
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::sse::{Event as SseEvent, KeepAlive, Sse};
use axum::routing::{get, post};
use axum::{Json, Router};
use futures_util::StreamExt;
use serde::Deserialize;
use tokio::sync::{Mutex, broadcast};
use tokio_stream::wrappers::BroadcastStream;

#[derive(Debug)]
enum ServerModel {
    Echo(EchoModel),
    Blend(BlendProvider),
}

impl ModelProvider for ServerModel {
    fn model_id(&self) -> Option<&str> {
        match self {
            Self::Echo(model) => model.model_id(),
            Self::Blend(model) => model.model_id(),
        }
    }

    fn model_registry_snapshot(&self) -> String {
        match self {
            Self::Blend(model) => model.model_registry_snapshot(),
            Self::Echo(model) => model.model_registry_snapshot(),
        }
    }

    fn supports_model_alias(&self, alias: &str) -> bool {
        match self {
            Self::Blend(model) => model.supports_model_alias(alias),
            Self::Echo(model) => model.supports_model_alias(alias),
        }
    }

    async fn complete(
        &mut self,
        request: ModelRunRequest,
    ) -> Result<ModelRunResult, ProviderError> {
        match self {
            Self::Echo(model) => model.complete(request).await,
            Self::Blend(model) => model.complete(request).await,
        }
    }

    async fn complete_with_model(
        &mut self,
        request: ModelRunRequest,
        model_alias: &str,
    ) -> Result<ModelRunResult, ProviderError> {
        match self {
            Self::Blend(model) => model.complete_with_model(request, model_alias).await,
            Self::Echo(model) => model.complete_with_model(request, model_alias).await,
        }
    }

    async fn cancel(&mut self, run_id: &arabica_protocol::RunId) -> Result<bool, ProviderError> {
        match self {
            Self::Echo(model) => model.cancel(run_id).await,
            Self::Blend(model) => model.cancel(run_id).await,
        }
    }
}

type LocalSessionManager = SessionManager<CoreRuntime<ServerModel, LocalRunner>>;

const DEFAULT_ARCHIVE_ROOT: &str = "target/arabica-runtime-memory";

fn local_file_archive_store() -> RuntimeArchiveStore {
    let root = std::env::var("ARABICA__ARCHIVE_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(DEFAULT_ARCHIVE_ROOT));
    RuntimeArchiveStore::File { root }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct EnvProviderConfig {
    api_type: Option<String>,
    base_url: String,
    api_key_env: Option<String>,
    max_tokens: Option<u32>,
    thinking: Option<String>,
    reasoning_effort: Option<String>,
    request_timeout_secs: Option<u64>,
    anthropic_cache_static_prefix: Option<bool>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct EnvModelConfig {
    provider: String,
    model_id: String,
}

fn provider_key_env(name: &str) -> String {
    let suffix = name
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() {
                character.to_ascii_uppercase()
            } else {
                '_'
            }
        })
        .collect::<String>();
    format!("ARABICA_PROVIDER_{suffix}_API_KEY")
}

fn blend_from_json(
    providers_json: &str,
    models_json: &str,
    default_alias: Option<String>,
    lookup: impl Fn(&str) -> Option<String>,
) -> Result<(BlendProvider, String), ProviderError> {
    let providers = serde_json::from_str::<BTreeMap<String, EnvProviderConfig>>(providers_json)
        .map_err(|_| ProviderError::new("invalid ARABICA__PROVIDERS_JSON"))?;
    let models = serde_json::from_str::<BTreeMap<String, EnvModelConfig>>(models_json)
        .map_err(|_| ProviderError::new("invalid ARABICA__BLEND_MODELS_JSON"))?;
    if providers.is_empty() || models.is_empty() {
        return Err(ProviderError::new(
            "ARABICA__PROVIDERS_JSON and ARABICA__BLEND_MODELS_JSON must not be empty",
        ));
    }
    let default_alias = default_alias
        .unwrap_or_else(|| models.keys().next().expect("models are non-empty").clone());
    let mut configs = BTreeMap::new();
    for (alias, model) in models {
        if alias.trim().is_empty()
            || model.provider.trim().is_empty()
            || model.model_id.trim().is_empty()
        {
            return Err(ProviderError::new(
                "model alias, provider, and model_id must not be empty",
            ));
        }
        let provider = providers.get(&model.provider).ok_or_else(|| {
            ProviderError::new(format!(
                "model alias {alias:?} references unknown provider {:?}",
                model.provider
            ))
        })?;
        if provider.base_url.trim().is_empty() {
            return Err(ProviderError::new(format!(
                "base_url is required for provider {:?}",
                model.provider
            )));
        }
        let api_type = provider
            .api_type
            .as_deref()
            .unwrap_or("open_ai_chat_completions")
            .parse::<ApiType>()?;
        if provider.max_tokens == Some(0) || provider.request_timeout_secs == Some(0) {
            return Err(ProviderError::new(format!(
                "max_tokens and request_timeout_secs must be positive for provider {:?}",
                model.provider
            )));
        }
        if provider.reasoning_effort.is_some() && provider.thinking.is_some() {
            return Err(ProviderError::new(format!(
                "configure only one of thinking or reasoning_effort for provider {:?}",
                model.provider
            )));
        }
        if provider.anthropic_cache_static_prefix == Some(true)
            && api_type != ApiType::AnthropicMessages
        {
            return Err(ProviderError::new(format!(
                "anthropic_cache_static_prefix is only supported for Anthropic provider {:?}",
                model.provider
            )));
        }
        let key_env = provider
            .api_key_env
            .clone()
            .unwrap_or_else(|| provider_key_env(&model.provider));
        if key_env.trim().is_empty() {
            return Err(ProviderError::new(format!(
                "api_key_env must not be empty for provider {:?}",
                model.provider
            )));
        }
        let api_key = lookup(&key_env)
            .filter(|key| !key.trim().is_empty())
            .ok_or_else(|| {
                ProviderError::new(format!(
                    "API key is required for provider {:?}; set {key_env}",
                    model.provider
                ))
            })?;
        let mut config =
            ApiProviderConfig::new(api_type, api_key, &provider.base_url, model.model_id);
        config.max_tokens = provider.max_tokens;
        config.request_timeout_secs = provider.request_timeout_secs.unwrap_or(300);
        config.anthropic_cache_static_prefix =
            provider.anthropic_cache_static_prefix.unwrap_or(false);
        if let Some(effort) = provider.reasoning_effort.as_deref() {
            match (api_type, effort) {
                (ApiType::OpenAiResponses, level @ ("low" | "medium" | "high")) => {
                    config.thinking_enabled = true;
                    config.reasoning_effort = Some(level.to_owned());
                }
                _ => {
                    return Err(ProviderError::new(format!(
                        "unsupported reasoning_effort {effort:?} for {api_type}"
                    )));
                }
            }
        }
        if let Some(thinking) = provider.thinking.as_deref() {
            match (api_type, thinking) {
                (_, "off") => {}
                (ApiType::OpenAiChatCompletions, "on") => config.thinking_enabled = true,
                (ApiType::OpenAiResponses, level @ ("low" | "medium" | "high")) => {
                    config.thinking_enabled = true;
                    config.reasoning_effort = Some(level.to_owned());
                }
                _ => {
                    return Err(ProviderError::new(format!(
                        "unsupported thinking level {thinking:?} for {api_type}"
                    )));
                }
            }
        }
        configs.insert(alias, config);
    }
    let provider = BlendProvider::from_configs(default_alias.clone(), configs)?;
    Ok((provider, default_alias))
}

#[derive(Clone)]
pub struct AppState {
    sessions: Arc<Mutex<LocalSessionManager>>,
    events: broadcast::Sender<EventEnvelope>,
    evaluation_home: Option<PathBuf>,
}

impl Default for AppState {
    fn default() -> Self {
        let (events, _) = broadcast::channel(512);
        let mut runtime = CoreRuntime::with_memory_configuration(
            ServerModel::Echo(EchoModel::default()),
            LocalRunner::new("."),
            ShortMemoryPolicy::default(),
            false,
            local_file_archive_store(),
        );
        runtime.set_compaction_strategy(RuntimeCompactionStrategy::FileBackedGc);
        runtime.set_async_file_backed_gc(true);
        Self {
            sessions: Arc::new(Mutex::new(SessionManager::new(runtime))),
            events,
            evaluation_home: None,
        }
    }
}

impl AppState {
    /// Explicitly opt in to reading a local host's persisted evaluations.
    pub fn with_evaluation_home(mut self, home: PathBuf) -> Self {
        self.evaluation_home = Some(home);
        self
    }
    pub fn from_api_env() -> Result<Self, ProviderError> {
        let tool_root = std::env::var("ARABICA__TOOL_ROOT").unwrap_or_else(|_| ".".to_owned());
        let providers_json = std::env::var("ARABICA__PROVIDERS_JSON")
            .map_err(|_| ProviderError::new("ARABICA__PROVIDERS_JSON is required"))?;
        let models_json = std::env::var("ARABICA__BLEND_MODELS_JSON")
            .map_err(|_| ProviderError::new("ARABICA__BLEND_MODELS_JSON is required"))?;
        let (provider, default_alias) = blend_from_json(
            &providers_json,
            &models_json,
            std::env::var("ARABICA__BLEND_DEFAULT").ok(),
            |name| std::env::var(name).ok(),
        )?;
        let recovery_after_no_progress_steps =
            std::env::var("ARABICA__BLEND_RECOVERY_AFTER_NO_PROGRESS_STEPS")
                .unwrap_or_else(|_| "2".to_owned())
                .parse::<usize>()
                .map_err(|error| {
                    ProviderError::new(format!(
                        "invalid ARABICA__BLEND_RECOVERY_AFTER_NO_PROGRESS_STEPS: {error}"
                    ))
                })?;
        let policy = BlendRoutingPolicy {
            policy_id: std::env::var("ARABICA__BLEND_POLICY_ID")
                .unwrap_or_else(|_| "env-rules".to_owned()),
            version: std::env::var("ARABICA__BLEND_POLICY_VERSION")
                .unwrap_or_else(|_| "1".to_owned())
                .parse::<u64>()
                .map_err(|error| {
                    ProviderError::new(format!("invalid ARABICA__BLEND_POLICY_VERSION: {error}"))
                })?,
            default_model: default_alias,
            after_tool_success: std::env::var("ARABICA__BLEND_AFTER_TOOL_SUCCESS").ok(),
            after_tool_error: std::env::var("ARABICA__BLEND_AFTER_TOOL_ERROR").ok(),
            recovery_model: std::env::var("ARABICA__BLEND_RECOVERY_MODEL").ok(),
            planning_model: None,
            tool_routes: Vec::new(),
            recovery_after_no_progress_steps,
            minimum_model_dwell_steps: std::env::var("ARABICA__BLEND_MINIMUM_MODEL_DWELL_STEPS")
                .unwrap_or_else(|_| "1".to_owned())
                .parse::<usize>()
                .map_err(|error| {
                    ProviderError::new(format!(
                        "invalid ARABICA__BLEND_MINIMUM_MODEL_DWELL_STEPS: {error}"
                    ))
                })?,
            tool_call_capable_models: std::env::var("ARABICA__BLEND_TOOL_CALL_MODELS")
                .unwrap_or_default()
                .split(',')
                .map(str::trim)
                .filter(|alias| !alias.is_empty())
                .map(str::to_owned)
                .collect(),
            typed_completion_capable_models: std::env::var(
                "ARABICA__BLEND_TYPED_COMPLETION_MODELS",
            )
            .unwrap_or_default()
            .split(',')
            .map(str::trim)
            .filter(|alias| !alias.is_empty())
            .map(str::to_owned)
            .collect(),
        };
        let (model, blend_policy) = (ServerModel::Blend(provider), Some(policy));
        let compaction_strategy = match std::env::var("ARABICA__COMPACTION_STRATEGY")
            .unwrap_or_else(|_| "file_backed_gc".to_owned())
            .to_ascii_lowercase()
            .as_str()
        {
            "disabled" => RuntimeCompactionStrategy::Disabled,
            "pointer_gc" => RuntimeCompactionStrategy::PointerGc,
            "file_backed_gc" | "fbgc" => RuntimeCompactionStrategy::FileBackedGc,
            value => {
                return Err(ProviderError::new(format!(
                    "invalid ARABICA__COMPACTION_STRATEGY {value}; expected disabled, pointer_gc, or file_backed_gc"
                )));
            }
        };
        let pgc_effort = std::env::var("COMPACTION_EFFORT")
            .or_else(|_| std::env::var("PGC_EFFORT"))
            .unwrap_or_else(|_| "1".to_owned())
            .parse::<usize>()
            .map_err(|error| ProviderError::new(format!("invalid COMPACTION_EFFORT: {error}")))?;
        if pgc_effort == 0 {
            return Err(ProviderError::new("COMPACTION_EFFORT must be positive"));
        }
        let pgc_continuation_probability_bps = std::env::var("PGC_CONTINUATION_PROBABILITY_BPS")
            .unwrap_or_else(|_| "7500".to_owned())
            .parse::<u32>()
            .map_err(|error| {
                ProviderError::new(format!("invalid PGC_CONTINUATION_PROBABILITY_BPS: {error}"))
            })?;
        if pgc_continuation_probability_bps > 10_000 {
            return Err(ProviderError::new(
                "PGC_CONTINUATION_PROBABILITY_BPS must be between 0 and 10000",
            ));
        }
        let mut runtime = CoreRuntime::with_memory_configuration(
            model,
            LocalRunner::new(tool_root),
            ShortMemoryPolicy::default(),
            false,
            local_file_archive_store(),
        );
        if let Some(policy) = blend_policy {
            runtime
                .set_blend_policy(Some(policy))
                .map_err(|error| ProviderError::new(error.to_string()))?;
        }
        runtime.set_compaction_strategy(compaction_strategy);
        runtime.set_async_file_backed_gc(true);
        runtime.set_pointer_gc_effort(pgc_effort);
        runtime.set_pointer_gc_continuation_probability_bps(pgc_continuation_probability_bps);
        let (events, _) = broadcast::channel(512);
        Ok(Self {
            sessions: Arc::new(Mutex::new(SessionManager::new(runtime))),
            events,
            evaluation_home: std::env::var_os("ARABICA__EVALUATION_HOME").map(PathBuf::from),
        })
    }
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/v1/schema", get(schema))
        .route("/v1/evaluations", get(query_evaluation))
        .route("/v1/commands", post(submit_command))
        .route("/v1/events", get(subscribe_events))
        .with_state(state)
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct EvaluationQuery {
    workspace_id: arabica_protocol::WorkspaceId,
    session_id: arabica_protocol::SessionId,
}

#[derive(serde::Serialize)]
struct EvaluationQueryError {
    code: &'static str,
}

async fn query_evaluation(
    State(state): State<AppState>,
    axum::extract::Query(query): axum::extract::Query<EvaluationQuery>,
) -> Result<Json<arabica_adapters::EvaluationView>, (StatusCode, Json<EvaluationQueryError>)> {
    let Some(home) = state.evaluation_home else {
        return Err((
            StatusCode::NOT_FOUND,
            Json(EvaluationQueryError {
                code: "evaluation_disabled",
            }),
        ));
    };
    tokio::task::spawn_blocking(move || {
        let report = arabica_adapters::SqliteEvaluationStore::read_latest(
            &home.join("evaluation.sqlite3"),
            &query.workspace_id,
            &query.session_id,
        )
        .map_err(|_| {
            (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(EvaluationQueryError {
                    code: "evaluation_unavailable",
                }),
            )
        })?;
        Ok(Json(arabica_adapters::EvaluationView {
            schema_version: 1,
            workspace_id: query.workspace_id,
            session_id: query.session_id,
            worker_status: None,
            refresh_accepted: None,
            report,
        }))
    })
    .await
    .map_err(|_| {
        (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(EvaluationQueryError {
                code: "evaluation_unavailable",
            }),
        )
    })?
}

async fn health() -> &'static str {
    "ok"
}

async fn schema() -> Json<serde_json::Value> {
    Json(serde_json::to_value(protocol_schema()).expect("protocol schema serializes"))
}

async fn submit_command(
    State(state): State<AppState>,
    Json(command): Json<CommandEnvelope>,
) -> Result<Json<Vec<EventEnvelope>>, (StatusCode, Json<CommandFailure>)> {
    let command_id = command.command_id.clone();
    let events = state
        .sessions
        .lock()
        .await
        .handle(command)
        .await
        .map_err(|error| {
            (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(CommandFailure::new(command_id, error.code, error.message)),
            )
        })?;

    for event in &events {
        let _ = state.events.send(event.clone());
    }
    Ok(Json(events))
}

async fn subscribe_events(
    State(state): State<AppState>,
) -> Sse<impl futures_util::Stream<Item = Result<SseEvent, Infallible>>> {
    let stream = BroadcastStream::new(state.events.subscribe()).filter_map(|message| async move {
        match message {
            Ok(event) => serde_json::to_string(&event)
                .ok()
                .map(|data| Ok(SseEvent::default().event("structure.event").data(data))),
            Err(_) => None,
        }
    });
    Sse::new(stream).keep_alive(KeepAlive::default())
}

#[cfg(test)]
mod tests {
    use super::*;
    use arabica_protocol::{Command, CommandId, Event, WorkspaceId};
    use axum::body::{Body, to_bytes};
    use axum::http::Request;
    use tower::ServiceExt;

    #[test]
    fn json_blend_config_builds_different_provider_dialects() {
        let providers = r#"{
            "openai": {"api_type":"open_ai_responses","base_url":"https://openai.example/v1","api_key_env":"OPENAI_KEY","max_tokens":2048,"thinking":"high"},
            "anthropic": {"api_type":"anthropic_messages","base_url":"https://anthropic.example/v1","request_timeout_secs":90,"anthropic_cache_static_prefix":true}
        }"#;
        let models = r#"{
            "fast": {"provider":"openai","model_id":"gpt-mini"},
            "strong": {"provider":"anthropic","model_id":"claude-opus"}
        }"#;
        let (blend, default) = blend_from_json(
            providers,
            models,
            Some("strong".to_owned()),
            |name| match name {
                "OPENAI_KEY" => Some("openai-env-key".to_owned()),
                "ARABICA_PROVIDER_ANTHROPIC_API_KEY" => Some("anthropic-env-key".to_owned()),
                _ => None,
            },
        )
        .unwrap();

        assert_eq!(default, "strong");
        assert_eq!(blend.model_id(), Some("claude-opus"));
        assert!(blend.supports_model_alias("fast"));
        assert!(blend.supports_model_alias("strong"));
        let snapshot = blend.model_registry_snapshot();
        assert!(!snapshot.contains("env-key"));
        assert!(!snapshot.contains("example"));
    }

    #[test]
    fn json_blend_config_rejects_missing_credentials_and_provider_references() {
        let providers = r#"{"p":{"base_url":"https://provider.example/v1"}}"#;
        let models = r#"{"default":{"provider":"p","model_id":"model"}}"#;
        let missing_key = blend_from_json(providers, models, None, |_| None).unwrap_err();
        assert!(
            missing_key
                .to_string()
                .contains("ARABICA_PROVIDER_P_API_KEY")
        );

        let unknown_provider = blend_from_json(
            providers,
            r#"{"default":{"provider":"missing","model_id":"model"}}"#,
            None,
            |_| Some("key".to_owned()),
        )
        .unwrap_err();
        assert!(unknown_provider.to_string().contains("unknown provider"));
    }

    #[tokio::test]
    async fn desktop_evaluation_query_does_not_lock_agent_or_create_database() {
        use tower::ServiceExt;
        let home =
            std::env::temp_dir().join(format!("arabica-desktop-evaluation-{}", std::process::id()));
        let state = AppState::default().with_evaluation_home(home.clone());
        let _guard = state.sessions.lock().await;
        let response = tokio::time::timeout(
            std::time::Duration::from_secs(1),
            app(state.clone()).oneshot(
                axum::http::Request::builder()
                    .uri("/v1/evaluations?workspace_id=workspace&session_id=session")
                    .body(axum::body::Body::empty())
                    .unwrap(),
            ),
        )
        .await
        .expect("evaluation query blocked on Agent session")
        .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = axum::body::to_bytes(response.into_body(), 1024 * 1024)
            .await
            .unwrap();
        let view: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(view["schema_version"], 1);
        assert!(view["report"].is_null());
        assert!(!home.join("evaluation.sqlite3").exists());
        let disabled = app(AppState::default())
            .oneshot(
                axum::http::Request::builder()
                    .uri("/v1/evaluations?workspace_id=w&session_id=s")
                    .body(axum::body::Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(disabled.status(), StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn server_uses_the_local_file_archive_adapter() {
        let state = AppState::default();
        let sessions = state.sessions.lock().await;
        assert!(matches!(
            sessions.runtime().archive_store(),
            RuntimeArchiveStore::File { .. }
        ));
    }

    #[tokio::test]
    async fn command_endpoint_returns_canonical_events() {
        let command = CommandEnvelope::new(
            CommandId::new("command-1"),
            None,
            Command::SessionCreate {
                workspace_id: WorkspaceId::new("workspace-1"),
            },
        );
        let response = app(AppState::default())
            .oneshot(
                Request::post("/v1/commands")
                    .header("content-type", "application/json")
                    .body(Body::from(
                        serde_json::to_vec(&command).expect("command serializes"),
                    ))
                    .expect("request builds"),
            )
            .await
            .expect("request succeeds");

        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), 1024 * 1024)
            .await
            .expect("body is readable");
        let events: Vec<EventEnvelope> = serde_json::from_slice(&body).expect("events deserialize");
        assert!(matches!(events[0].event, Event::SessionCreated { .. }));
        assert_eq!(events[0].sequence, 1);
    }

    #[tokio::test]
    async fn invalid_protocol_version_returns_a_protocol_failure() {
        let mut command = CommandEnvelope::new(
            CommandId::new("command-1"),
            None,
            Command::SessionCreate {
                workspace_id: WorkspaceId::new("workspace-1"),
            },
        );
        command.protocol_version = "99".to_owned();
        let response = app(AppState::default())
            .oneshot(
                Request::post("/v1/commands")
                    .header("content-type", "application/json")
                    .body(Body::from(
                        serde_json::to_vec(&command).expect("command serializes"),
                    ))
                    .expect("request builds"),
            )
            .await
            .expect("request succeeds");

        assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
        let body = to_bytes(response.into_body(), 1024 * 1024)
            .await
            .expect("body is readable");
        let failure: CommandFailure = serde_json::from_slice(&body).expect("failure deserializes");
        assert_eq!(failure.command_id, CommandId::new("command-1"));
    }

    #[tokio::test]
    async fn schema_endpoint_exports_the_protocol_source_of_truth() {
        let response = app(AppState::default())
            .oneshot(
                Request::get("/v1/schema")
                    .body(Body::empty())
                    .expect("request builds"),
            )
            .await
            .expect("request succeeds");

        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), 1024 * 1024)
            .await
            .expect("body is readable");
        let schema: serde_json::Value = serde_json::from_slice(&body).expect("schema deserializes");
        assert_eq!(schema["title"], "ProtocolSchemaDocument");
        assert!(schema["$defs"]["CommandEnvelope"].is_object());
        assert!(schema["$defs"]["EventEnvelope"].is_object());
    }
}
