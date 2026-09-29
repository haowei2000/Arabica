//! HTTP + SSE binding for the canonical Structure protocol.

use std::convert::Infallible;
use std::path::PathBuf;
use std::sync::Arc;

use arabica_protocol::{CommandEnvelope, CommandFailure, EventEnvelope, protocol_schema};
use arabica_provider::{
    ApiModelProvider, ApiProviderConfig, ApiType, BlendProvider, EchoModel, ModelProvider,
    ModelRunRequest, ModelRunResult, ProviderError,
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
use tokio::sync::{Mutex, broadcast};
use tokio_stream::wrappers::BroadcastStream;

#[derive(Debug)]
enum ServerModel {
    Echo(EchoModel),
    Api(ApiModelProvider),
    Blend(BlendProvider),
}

impl ModelProvider for ServerModel {
    fn model_id(&self) -> Option<&str> {
        match self {
            Self::Echo(model) => model.model_id(),
            Self::Api(model) => model.model_id(),
            Self::Blend(model) => model.model_id(),
        }
    }

    fn model_registry_snapshot(&self) -> String {
        match self {
            Self::Blend(model) => model.model_registry_snapshot(),
            Self::Echo(model) => model.model_registry_snapshot(),
            Self::Api(model) => model.model_registry_snapshot(),
        }
    }

    fn supports_model_alias(&self, alias: &str) -> bool {
        match self {
            Self::Blend(model) => model.supports_model_alias(alias),
            Self::Echo(model) => model.supports_model_alias(alias),
            Self::Api(model) => model.supports_model_alias(alias),
        }
    }

    async fn complete(
        &mut self,
        request: ModelRunRequest,
    ) -> Result<ModelRunResult, ProviderError> {
        match self {
            Self::Echo(model) => model.complete(request).await,
            Self::Api(model) => model.complete(request).await,
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
            Self::Api(model) => model.complete_with_model(request, model_alias).await,
        }
    }

    async fn cancel(&mut self, run_id: &arabica_protocol::RunId) -> Result<bool, ProviderError> {
        match self {
            Self::Echo(model) => model.cancel(run_id).await,
            Self::Api(model) => model.cancel(run_id).await,
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

fn blend_candidates(value: &str) -> Result<Vec<(String, String)>, ProviderError> {
    let candidates = value
        .split(',')
        .map(|entry| {
            let (alias, model) = entry.trim().split_once('=')?;
            let alias = alias.trim();
            let model = model.trim();
            (!alias.is_empty() && !model.is_empty()).then(|| (alias.to_owned(), model.to_owned()))
        })
        .collect::<Option<Vec<_>>>();
    let candidates = candidates.ok_or_else(|| {
        ProviderError::new(
            "invalid ARABICA__BLEND_MODELS; expected comma-separated alias=model entries",
        )
    })?;
    if candidates.is_empty() {
        return Err(ProviderError::new(
            "ARABICA__BLEND_MODELS must not be empty",
        ));
    }
    Ok(candidates)
}

#[derive(Clone)]
pub struct AppState {
    sessions: Arc<Mutex<LocalSessionManager>>,
    events: broadcast::Sender<EventEnvelope>,
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
        }
    }
}

impl AppState {
    pub fn from_api_env() -> Result<Self, ProviderError> {
        let api_type = std::env::var("ARABICA__API_TYPE")
            .unwrap_or_else(|_| ApiType::OpenAiChatCompletions.to_string())
            .parse::<ApiType>()?;
        let api_key = std::env::var("OPENAI__API_KEY")
            .map_err(|_| ProviderError::new("OPENAI__API_KEY is required"))?;
        let base_url = std::env::var("OPENAI__BASE_URL")
            .map_err(|_| ProviderError::new("OPENAI__BASE_URL is required"))?;
        let tool_root = std::env::var("ARABICA__TOOL_ROOT").unwrap_or_else(|_| ".".to_owned());
        let (model, blend_policy) = match std::env::var("ARABICA__BLEND_MODELS") {
            Ok(configured_models) => {
                let candidates = blend_candidates(&configured_models)?;
                let default_alias = std::env::var("ARABICA__BLEND_DEFAULT")
                    .unwrap_or_else(|_| candidates[0].0.clone());
                let base_model = candidates[0].1.clone();
                let provider = BlendProvider::from_shared_config(
                    ApiProviderConfig::new(api_type, api_key, base_url, base_model),
                    default_alias.clone(),
                    candidates,
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
                            ProviderError::new(format!(
                                "invalid ARABICA__BLEND_POLICY_VERSION: {error}"
                            ))
                        })?,
                    default_model: default_alias,
                    after_tool_success: std::env::var("ARABICA__BLEND_AFTER_TOOL_SUCCESS").ok(),
                    after_tool_error: std::env::var("ARABICA__BLEND_AFTER_TOOL_ERROR").ok(),
                    recovery_model: std::env::var("ARABICA__BLEND_RECOVERY_MODEL").ok(),
                    recovery_after_no_progress_steps,
                    minimum_model_dwell_steps: std::env::var(
                        "ARABICA__BLEND_MINIMUM_MODEL_DWELL_STEPS",
                    )
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
                (ServerModel::Blend(provider), Some(policy))
            }
            Err(_) => {
                let model = std::env::var("OPENAI__MODEL")
                    .map_err(|_| ProviderError::new("OPENAI__MODEL is required"))?;
                (
                    ServerModel::Api(ApiModelProvider::new(ApiProviderConfig::new(
                        api_type, api_key, base_url, model,
                    ))?),
                    None,
                )
            }
        };
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
        })
    }
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/v1/schema", get(schema))
        .route("/v1/commands", post(submit_command))
        .route("/v1/events", get(subscribe_events))
        .with_state(state)
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
