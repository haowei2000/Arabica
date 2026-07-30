//! HTTP + SSE binding for the canonical Structure protocol.

use std::convert::Infallible;
use std::path::PathBuf;
use std::sync::Arc;

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::sse::{Event as SseEvent, KeepAlive, Sse};
use axum::routing::{get, post};
use axum::{Json, Router};
use futures_util::StreamExt;
use structure_protocol::{CommandEnvelope, CommandFailure, EventEnvelope, protocol_schema};
use structure_provider::{
    ApiModelProvider, ApiProviderConfig, ApiType, EchoModel, ModelProvider, ModelRunRequest,
    ModelRunResult, ProviderError,
};
use structure_runner::LocalRunner;
use structure_runtime::{CoreRuntime, RuntimeArchiveStore, ShortMemoryPolicy};
use structure_session::SessionManager;
use tokio::sync::{Mutex, broadcast};
use tokio_stream::wrappers::BroadcastStream;

#[derive(Debug)]
enum ServerModel {
    Echo(EchoModel),
    Api(ApiModelProvider),
}

impl ModelProvider for ServerModel {
    async fn complete(
        &mut self,
        request: ModelRunRequest,
    ) -> Result<ModelRunResult, ProviderError> {
        match self {
            Self::Echo(model) => model.complete(request).await,
            Self::Api(model) => model.complete(request).await,
        }
    }

    async fn cancel(&mut self, run_id: &structure_protocol::RunId) -> Result<bool, ProviderError> {
        match self {
            Self::Echo(model) => model.cancel(run_id).await,
            Self::Api(model) => model.cancel(run_id).await,
        }
    }
}

type LocalSessionManager = SessionManager<CoreRuntime<ServerModel, LocalRunner>>;

const DEFAULT_ARCHIVE_ROOT: &str = "target/structure-runtime-memory";

fn local_file_archive_store() -> RuntimeArchiveStore {
    let root = std::env::var("STRUCTURE__ARCHIVE_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(DEFAULT_ARCHIVE_ROOT));
    RuntimeArchiveStore::File { root }
}

#[derive(Clone)]
pub struct AppState {
    sessions: Arc<Mutex<LocalSessionManager>>,
    events: broadcast::Sender<EventEnvelope>,
}

impl Default for AppState {
    fn default() -> Self {
        let (events, _) = broadcast::channel(512);
        let runtime = CoreRuntime::with_memory_configuration(
            ServerModel::Echo(EchoModel::default()),
            LocalRunner::new("."),
            ShortMemoryPolicy::default(),
            true,
            local_file_archive_store(),
        );
        Self {
            sessions: Arc::new(Mutex::new(SessionManager::new(runtime))),
            events,
        }
    }
}

impl AppState {
    pub fn from_api_env() -> Result<Self, ProviderError> {
        let api_type = std::env::var("STRUCTURE__API_TYPE")
            .unwrap_or_else(|_| ApiType::OpenAiChatCompletions.to_string())
            .parse::<ApiType>()?;
        let api_key = std::env::var("OPENAI__API_KEY")
            .map_err(|_| ProviderError::new("OPENAI__API_KEY is required"))?;
        let base_url = std::env::var("OPENAI__BASE_URL")
            .map_err(|_| ProviderError::new("OPENAI__BASE_URL is required"))?;
        let model = std::env::var("OPENAI__MODEL")
            .map_err(|_| ProviderError::new("OPENAI__MODEL is required"))?;
        let tool_root = std::env::var("STRUCTURE__TOOL_ROOT").unwrap_or_else(|_| ".".to_owned());
        let provider =
            ApiModelProvider::new(ApiProviderConfig::new(api_type, api_key, base_url, model))?;
        let pgc_effort = std::env::var("PGC_EFFORT")
            .unwrap_or_else(|_| "1".to_owned())
            .parse::<usize>()
            .map_err(|error| ProviderError::new(format!("invalid PGC_EFFORT: {error}")))?;
        if pgc_effort == 0 {
            return Err(ProviderError::new("PGC_EFFORT must be positive"));
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
            ServerModel::Api(provider),
            LocalRunner::new(tool_root),
            ShortMemoryPolicy::default(),
            true,
            local_file_archive_store(),
        );
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
    use axum::body::{Body, to_bytes};
    use axum::http::Request;
    use structure_protocol::{Command, CommandId, Event, WorkspaceId};
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
