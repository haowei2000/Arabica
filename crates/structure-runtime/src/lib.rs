//! Headless runtime core.
//!
//! Runtime projects session events into ephemeral short memory, resolves
//! workspace-scoped long memory, orchestrates model/tool turns, and normalizes
//! runner execution output into canonical protocol events. Session identity,
//! scheduling, event persistence,
//! and event sequencing belong to `structure-session`.

mod long_memory;
mod short_memory;

use std::collections::HashMap;
use std::error::Error;
use std::fmt::{Display, Formatter};

pub use long_memory::{LongMemoryError, LongMemoryErrorKind, LongMemoryManager};
pub use short_memory::ShortMemoryProjector;
use structure_model::{ContentBlock, RuntimeItem, ToolChoice, ToolDefinition, ToolResultItem};
use structure_protocol::{
    Command, ContextEntry, DisclosureLevel, Event, EventEnvelope, OutputStream, RunId, SessionId,
    WorkspaceId,
};
use structure_provider::{ModelProvider, ModelRunRequest};
use structure_runner::{RunnerEnvironment, RunnerOutput, ToolExecutionRequest};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RuntimeErrorKind {
    SessionAlreadyOpen,
    SessionNotOpen,
    TargetSessionAlreadyOpen,
    MissingRunId,
    MismatchedRunId,
    InvalidInput,
    InvalidLongMemory,
    LongMemoryNotFound,
    RunNotActive,
    Cancellation,
    UnsupportedCommand,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RuntimeError {
    kind: RuntimeErrorKind,
    message: String,
}

impl RuntimeError {
    pub fn new(kind: RuntimeErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }

    pub fn kind(&self) -> RuntimeErrorKind {
        self.kind
    }
}

impl Display for RuntimeError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        self.message.fmt(formatter)
    }
}

impl Error for RuntimeError {}

#[allow(async_fn_in_trait)]
pub trait RuntimeEngine {
    fn open_session(
        &mut self,
        session_id: &SessionId,
        workspace_id: &WorkspaceId,
    ) -> Result<(), RuntimeError>;
    fn fork_session(
        &mut self,
        source_session_id: &SessionId,
        target_session_id: &SessionId,
    ) -> Result<(), RuntimeError>;
    fn close_session(&mut self, session_id: &SessionId) -> Result<(), RuntimeError>;
    async fn handle(
        &mut self,
        session_id: &SessionId,
        run_id: Option<&RunId>,
        history: &[EventEnvelope],
        command: &Command,
    ) -> Result<Vec<Event>, RuntimeError>;
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RuntimeSession {
    pub workspace_id: WorkspaceId,
    pub disclosure: DisclosureLevel,
}

#[derive(Debug)]
pub struct CoreRuntime<M, R> {
    sessions: HashMap<SessionId, RuntimeSession>,
    long_memory: HashMap<WorkspaceId, LongMemoryManager>,
    model: M,
    runner: R,
}

impl<M, R> CoreRuntime<M, R> {
    pub fn new(model: M, runner: R) -> Self {
        Self {
            sessions: HashMap::new(),
            long_memory: HashMap::new(),
            model,
            runner,
        }
    }

    pub fn session(&self, session_id: &SessionId) -> Option<&RuntimeSession> {
        self.sessions.get(session_id)
    }

    pub fn long_memory(&self, workspace_id: &WorkspaceId) -> Option<&LongMemoryManager> {
        self.long_memory.get(workspace_id)
    }

    pub fn model(&self) -> &M {
        &self.model
    }

    pub fn model_mut(&mut self) -> &mut M {
        &mut self.model
    }

    pub fn runner(&self) -> &R {
        &self.runner
    }

    pub fn runner_mut(&mut self) -> &mut R {
        &mut self.runner
    }

    pub fn is_session_open(&self, session_id: &SessionId) -> bool {
        self.sessions.contains_key(session_id)
    }

    fn session_mut(&mut self, session_id: &SessionId) -> Result<&mut RuntimeSession, RuntimeError> {
        self.sessions
            .get_mut(session_id)
            .ok_or_else(|| session_not_open(session_id))
    }

    fn session_ref(&self, session_id: &SessionId) -> Result<&RuntimeSession, RuntimeError> {
        self.sessions
            .get(session_id)
            .ok_or_else(|| session_not_open(session_id))
    }

    fn session_memory(
        &self,
        session_id: &SessionId,
    ) -> Result<(&LongMemoryManager, DisclosureLevel), RuntimeError> {
        let session = self.session_ref(session_id)?;
        let memory = self.long_memory.get(&session.workspace_id).ok_or_else(|| {
            RuntimeError::new(
                RuntimeErrorKind::SessionNotOpen,
                format!(
                    "workspace long memory {} is not open for session {session_id}",
                    session.workspace_id
                ),
            )
        })?;
        Ok((memory, session.disclosure))
    }

    fn session_memory_mut(
        &mut self,
        session_id: &SessionId,
    ) -> Result<&mut LongMemoryManager, RuntimeError> {
        let workspace_id = self.session_ref(session_id)?.workspace_id.clone();
        self.long_memory.get_mut(&workspace_id).ok_or_else(|| {
            RuntimeError::new(
                RuntimeErrorKind::SessionNotOpen,
                format!(
                    "workspace long memory {workspace_id} is not open for session {session_id}"
                ),
            )
        })
    }
}

impl<M: ModelProvider, R: RunnerEnvironment> RuntimeEngine for CoreRuntime<M, R> {
    fn open_session(
        &mut self,
        session_id: &SessionId,
        workspace_id: &WorkspaceId,
    ) -> Result<(), RuntimeError> {
        if self.sessions.contains_key(session_id) {
            return Err(RuntimeError::new(
                RuntimeErrorKind::SessionAlreadyOpen,
                format!("runtime session {session_id} is already open"),
            ));
        }
        self.long_memory.entry(workspace_id.clone()).or_default();
        self.sessions.insert(
            session_id.clone(),
            RuntimeSession {
                workspace_id: workspace_id.clone(),
                disclosure: DisclosureLevel::Overview,
            },
        );
        Ok(())
    }

    fn fork_session(
        &mut self,
        source_session_id: &SessionId,
        target_session_id: &SessionId,
    ) -> Result<(), RuntimeError> {
        if self.sessions.contains_key(target_session_id) {
            return Err(RuntimeError::new(
                RuntimeErrorKind::TargetSessionAlreadyOpen,
                format!("runtime target session {target_session_id} is already open"),
            ));
        }
        let session = self
            .sessions
            .get(source_session_id)
            .cloned()
            .ok_or_else(|| session_not_open(source_session_id))?;
        self.sessions.insert(target_session_id.clone(), session);
        Ok(())
    }

    fn close_session(&mut self, session_id: &SessionId) -> Result<(), RuntimeError> {
        self.sessions
            .remove(session_id)
            .map(|_| ())
            .ok_or_else(|| session_not_open(session_id))
    }

    async fn handle(
        &mut self,
        session_id: &SessionId,
        run_id: Option<&RunId>,
        history: &[EventEnvelope],
        command: &Command,
    ) -> Result<Vec<Event>, RuntimeError> {
        self.session_ref(session_id)?;
        match command {
            Command::MessageSend { content } => {
                let run_id = run_id.ok_or_else(|| {
                    RuntimeError::new(
                        RuntimeErrorKind::MissingRunId,
                        "message.send requires a scheduled run identifier",
                    )
                })?;
                if content.trim().is_empty() {
                    return Err(RuntimeError::new(
                        RuntimeErrorKind::InvalidInput,
                        "message.send content must not be empty",
                    ));
                }
                let (long_memory, disclosure) = self.session_memory(session_id)?;
                let long_memory = long_memory.entries(disclosure);
                let short_memory = ShortMemoryProjector::project(history);
                let mut events = vec![
                    Event::RunStarted,
                    Event::MessageAccepted {
                        content: content.clone(),
                    },
                ];
                let tools = vec![write_file_definition()];
                let mut continuation = Vec::new();
                for _ in 0..8 {
                    let result = self
                        .model
                        .complete(ModelRunRequest {
                            session_id: session_id.clone(),
                            run_id: run_id.clone(),
                            input: content.clone(),
                            short_memory: short_memory.clone(),
                            long_memory: long_memory.clone(),
                            tools: tools.clone(),
                            tool_choice: ToolChoice::Auto,
                            continuation: continuation.clone(),
                            disclosure,
                        })
                        .await;
                    let result = match result {
                        Ok(result) => result,
                        Err(error) => {
                            events.push(Event::RunFailed {
                                message: format!("model provider failed: {error}"),
                            });
                            return Ok(events);
                        }
                    };
                    let response_items = result
                        .response
                        .as_ref()
                        .map(|response| response.items.clone())
                        .unwrap_or_default();
                    let tool_calls: Vec<_> = response_items
                        .iter()
                        .filter_map(|item| match item {
                            RuntimeItem::ToolCall(call) => Some(call.clone()),
                            _ => None,
                        })
                        .collect();
                    if tool_calls.is_empty() {
                        events.push(Event::RunCompleted {
                            output: result.final_output,
                        });
                        return Ok(events);
                    }

                    continuation.extend(response_items);
                    for call in tool_calls {
                        events.push(Event::ToolCallRequested {
                            call_id: call.call_id.clone(),
                            name: call.name.clone(),
                            arguments: call.arguments.clone(),
                        });
                        let execution = self
                            .runner
                            .execute(ToolExecutionRequest {
                                run_id: run_id.clone(),
                                call: call.clone(),
                            })
                            .await;
                        let execution = match execution {
                            Ok(execution) => execution,
                            Err(error) => {
                                events.push(Event::RunFailed {
                                    message: format!("runner failed: {error}"),
                                });
                                return Ok(events);
                            }
                        };
                        events.extend(execution.output.into_iter().map(|output| match output {
                            RunnerOutput::Stdout(chunk) => Event::CommandOutput {
                                stream: OutputStream::Stdout,
                                chunk,
                            },
                            RunnerOutput::Stderr(chunk) => Event::CommandOutput {
                                stream: OutputStream::Stderr,
                                chunk,
                            },
                        }));
                        let tool_result = execution.result;
                        let result_text = tool_result_text(&tool_result);
                        events.push(Event::ToolCallCompleted {
                            call_id: call.call_id.clone(),
                            name: call.name,
                            result: result_text,
                            is_error: tool_result.is_error,
                        });
                        continuation.push(RuntimeItem::ToolResult(tool_result));
                    }
                }
                events.push(Event::RunFailed {
                    message: "agent loop exceeded 8 model steps".to_owned(),
                });
                Ok(events)
            }
            Command::RunCancel {
                run_id: command_run_id,
            } => {
                let envelope_run_id = run_id.ok_or_else(|| {
                    RuntimeError::new(
                        RuntimeErrorKind::MissingRunId,
                        "run.cancel requires an envelope run identifier",
                    )
                })?;
                if envelope_run_id != command_run_id {
                    return Err(RuntimeError::new(
                        RuntimeErrorKind::MismatchedRunId,
                        format!(
                            "run.cancel target {command_run_id} does not match envelope run {envelope_run_id}"
                        ),
                    ));
                }
                let model_cancelled = self.model.cancel(command_run_id).await.map_err(|error| {
                    RuntimeError::new(
                        RuntimeErrorKind::Cancellation,
                        format!("model cancellation failed: {error}"),
                    )
                })?;
                let runner_cancelled =
                    self.runner.cancel(command_run_id).await.map_err(|error| {
                        RuntimeError::new(
                            RuntimeErrorKind::Cancellation,
                            format!("runner cancellation failed: {error}"),
                        )
                    })?;
                if !model_cancelled && !runner_cancelled {
                    return Err(RuntimeError::new(
                        RuntimeErrorKind::RunNotActive,
                        format!("no model provider or runner has active run {command_run_id}"),
                    ));
                }
                Ok(vec![Event::RunCancelled])
            }
            Command::ContextRead { path } => {
                let (memory, disclosure) = self.session_memory(session_id)?;
                let entry = memory
                    .read(path, disclosure)
                    .map_err(long_memory_error)?
                    .ok_or_else(|| {
                        RuntimeError::new(
                            RuntimeErrorKind::LongMemoryNotFound,
                            format!("context path not found: {path}"),
                        )
                    })?;
                Ok(vec![Event::ContextRead { entry }])
            }
            Command::ContextSearch { query } => {
                let (memory, disclosure) = self.session_memory(session_id)?;
                let entries = memory.search(query, disclosure);
                Ok(vec![Event::ContextSearchResult { entries }])
            }
            Command::ContextUpdate { path, content } => {
                let entry: ContextEntry = self
                    .session_memory_mut(session_id)?
                    .update(path.clone(), content.clone())
                    .map_err(long_memory_error)?;
                Ok(vec![Event::ContextUpdated { entry }])
            }
            Command::ContextDelete { path } => {
                let deleted_path = self
                    .session_memory_mut(session_id)?
                    .delete(path)
                    .map_err(long_memory_error)?
                    .ok_or_else(|| {
                        RuntimeError::new(
                            RuntimeErrorKind::LongMemoryNotFound,
                            format!("context path not found: {path}"),
                        )
                    })?;
                Ok(vec![Event::ContextDeleted { path: deleted_path }])
            }
            Command::ContextSetDisclosure { level } => {
                self.session_mut(session_id)?.disclosure = *level;
                Ok(vec![Event::ContextDisclosureSet { level: *level }])
            }
            Command::SessionCreate { .. }
            | Command::SessionFork { .. }
            | Command::SessionResume
            | Command::SessionSuspend
            | Command::SessionClose => Err(RuntimeError::new(
                RuntimeErrorKind::UnsupportedCommand,
                "session lifecycle commands must be handled by session management",
            )),
        }
    }
}

fn session_not_open(session_id: &SessionId) -> RuntimeError {
    RuntimeError::new(
        RuntimeErrorKind::SessionNotOpen,
        format!("runtime session {session_id} is not open"),
    )
}

fn long_memory_error(error: LongMemoryError) -> RuntimeError {
    RuntimeError::new(RuntimeErrorKind::InvalidLongMemory, error.to_string())
}

fn write_file_definition() -> ToolDefinition {
    ToolDefinition {
        name: "write_file".to_owned(),
        description: "Write UTF-8 text to a relative path inside the configured workspace root. Parent directories must already exist.".to_owned(),
        input_schema: serde_json::json!({
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative file path inside the workspace root"
                },
                "content": {
                    "type": "string",
                    "description": "Complete UTF-8 file content"
                }
            },
            "required": ["path", "content"],
            "additionalProperties": false
        }),
        strict: None,
    }
}

fn tool_result_text(result: &ToolResultItem) -> String {
    result
        .content
        .iter()
        .filter_map(|block| match block {
            ContentBlock::Text { text } => Some(text.as_str()),
            _ => None,
        })
        .collect::<Vec<_>>()
        .join("")
}

#[cfg(test)]
mod tests {
    use super::*;
    use structure_protocol::{CommandId, DisclosureLevel, EventId, EventMetadata};
    use structure_provider::{
        EchoModel, ModelProvider, ModelRunRequest, ModelRunResult, ProviderError, ShortMemoryEntry,
        ShortMemoryItem,
    };
    use structure_runner::{
        NoopRunner, RunnerEnvironment, RunnerError, RunnerOutput, ToolExecutionRequest,
        ToolExecutionResult,
    };

    #[derive(Debug)]
    struct RecordingModel {
        request: Option<ModelRunRequest>,
        result: Result<ModelRunResult, ProviderError>,
        cancel_result: Result<bool, ProviderError>,
    }

    impl RecordingModel {
        fn successful() -> Self {
            Self {
                request: None,
                result: Ok(ModelRunResult {
                    final_output: Some("done".to_owned()),
                    response: None,
                }),
                cancel_result: Ok(true),
            }
        }
    }

    impl ModelProvider for RecordingModel {
        async fn complete(
            &mut self,
            request: ModelRunRequest,
        ) -> Result<ModelRunResult, ProviderError> {
            self.request = Some(request);
            self.result.clone()
        }

        async fn cancel(&mut self, _run_id: &RunId) -> Result<bool, ProviderError> {
            self.cancel_result.clone()
        }
    }

    fn history_event(sequence: u64, event: Event) -> EventEnvelope {
        EventEnvelope::new(
            EventMetadata {
                event_id: EventId::new(format!("event-{sequence}")),
                command_id: CommandId::new("prior-command"),
                workspace_id: WorkspaceId::new("workspace-1"),
                session_id: SessionId::new("session-1"),
                run_id: Some(RunId::new("prior-run")),
                sequence,
                occurred_at_ms: 0,
            },
            event,
        )
    }

    #[tokio::test]
    async fn runtime_owns_long_memory_but_not_event_envelopes() {
        let session_id = SessionId::new("session-1");
        let workspace_id = WorkspaceId::new("workspace-1");
        let mut runtime = CoreRuntime::new(EchoModel::default(), NoopRunner);
        runtime
            .open_session(&session_id, &workspace_id)
            .expect("runtime session opens");

        let events = runtime
            .handle(
                &session_id,
                None,
                &[],
                &Command::ContextUpdate {
                    path: "memory/preference".to_owned(),
                    content: "concise".to_owned(),
                },
            )
            .await
            .expect("context update succeeds");

        assert!(matches!(events.as_slice(), [Event::ContextUpdated { .. }]));
        assert_eq!(
            runtime
                .long_memory(&workspace_id)
                .expect("long memory exists")
                .read("memory/preference", DisclosureLevel::Detail)
                .expect("valid context path"),
            Some(ContextEntry {
                path: "memory/preference".to_owned(),
                content: "concise".to_owned(),
            })
        );

        runtime
            .handle(
                &session_id,
                None,
                &[],
                &Command::ContextSetDisclosure {
                    level: DisclosureLevel::Detail,
                },
            )
            .await
            .expect("disclosure update succeeds");
        assert_eq!(
            runtime
                .session(&session_id)
                .expect("runtime session exists")
                .disclosure,
            DisclosureLevel::Detail
        );
    }

    #[tokio::test]
    async fn runtime_completes_a_text_only_model_turn() {
        let session_id = SessionId::new("session-1");
        let run_id = RunId::new("run-1");
        let mut runtime = CoreRuntime::new(EchoModel::default(), NoopRunner);
        runtime
            .open_session(&session_id, &WorkspaceId::new("workspace-1"))
            .expect("runtime session opens");

        let events = runtime
            .handle(
                &session_id,
                Some(&run_id),
                &[],
                &Command::MessageSend {
                    content: "hello".to_owned(),
                },
            )
            .await
            .expect("message succeeds");

        assert_eq!(events.first(), Some(&Event::RunStarted));
        assert_eq!(
            events.last(),
            Some(&Event::RunCompleted {
                output: Some("hello".to_owned())
            })
        );
    }

    #[tokio::test]
    async fn runtime_resolves_memory_before_calling_model_provider() {
        let session_id = SessionId::new("session-1");
        let run_id = RunId::new("run-1");
        let model = RecordingModel::successful();
        let mut runtime = CoreRuntime::new(model, NoopRunner);
        runtime
            .open_session(&session_id, &WorkspaceId::new("workspace-1"))
            .expect("runtime session opens");
        runtime
            .handle(
                &session_id,
                None,
                &[],
                &Command::ContextUpdate {
                    path: "/knowledge/design/".to_owned(),
                    content: "protocol first".to_owned(),
                },
            )
            .await
            .expect("context update succeeds");
        runtime
            .handle(
                &session_id,
                None,
                &[],
                &Command::ContextSetDisclosure {
                    level: DisclosureLevel::Glance,
                },
            )
            .await
            .expect("disclosure update succeeds");

        let history = vec![history_event(
            1,
            Event::MessageAccepted {
                content: "earlier".to_owned(),
            },
        )];
        runtime
            .handle(
                &session_id,
                Some(&run_id),
                &history,
                &Command::MessageSend {
                    content: "continue".to_owned(),
                },
            )
            .await
            .expect("message succeeds");

        let request = runtime
            .model()
            .request
            .as_ref()
            .expect("model provider received request");
        assert_eq!(request.disclosure, DisclosureLevel::Glance);
        assert_eq!(
            request.long_memory,
            vec![ContextEntry {
                path: "knowledge/design".to_owned(),
                content: "protocol first".to_owned(),
            }]
        );
        assert_eq!(
            request.short_memory,
            vec![ShortMemoryEntry {
                session_id: SessionId::new("session-1"),
                sequence: 1,
                run_id: Some(RunId::new("prior-run")),
                item: ShortMemoryItem::UserMessage {
                    content: "earlier".to_owned(),
                },
            }]
        );
    }

    #[tokio::test]
    async fn runner_output_order_is_preserved() {
        #[derive(Debug, Default)]
        struct ToolCallingModel {
            step: u8,
        }

        impl ModelProvider for ToolCallingModel {
            async fn complete(
                &mut self,
                _request: ModelRunRequest,
            ) -> Result<ModelRunResult, ProviderError> {
                self.step += 1;
                if self.step == 1 {
                    return Ok(ModelRunResult {
                        final_output: None,
                        response: Some(structure_model::RuntimeResponse {
                            items: vec![RuntimeItem::ToolCall(structure_model::ToolCallItem {
                                id: None,
                                call_id: "call-1".to_owned(),
                                name: "test_tool".to_owned(),
                                arguments: serde_json::json!({}),
                                provider_state: None,
                            })],
                            finish_reason: Some(structure_model::FinishReason::ToolCalls),
                            usage: structure_model::RuntimeUsage::default(),
                        }),
                    });
                }
                Ok(ModelRunResult {
                    final_output: Some("done".to_owned()),
                    response: None,
                })
            }

            async fn cancel(&mut self, _run_id: &RunId) -> Result<bool, ProviderError> {
                Ok(false)
            }
        }

        #[derive(Debug, Default)]
        struct OutputRunner;

        impl RunnerEnvironment for OutputRunner {
            async fn execute(
                &mut self,
                request: ToolExecutionRequest,
            ) -> Result<ToolExecutionResult, RunnerError> {
                Ok(ToolExecutionResult {
                    result: ToolResultItem {
                        id: None,
                        call_id: request.call.call_id,
                        name: Some(request.call.name),
                        content: vec![ContentBlock::text("ok")],
                        is_error: false,
                    },
                    output: vec![
                        RunnerOutput::Stdout("one".to_owned()),
                        RunnerOutput::Stderr("two".to_owned()),
                        RunnerOutput::Stdout("three".to_owned()),
                    ],
                })
            }

            async fn cancel(&mut self, _run_id: &RunId) -> Result<bool, RunnerError> {
                Ok(false)
            }
        }

        let session_id = SessionId::new("session-1");
        let run_id = RunId::new("run-1");
        let mut runtime = CoreRuntime::new(ToolCallingModel::default(), OutputRunner);
        runtime
            .open_session(&session_id, &WorkspaceId::new("workspace-1"))
            .expect("runtime session opens");

        let events = runtime
            .handle(
                &session_id,
                Some(&run_id),
                &[],
                &Command::MessageSend {
                    content: "run".to_owned(),
                },
            )
            .await
            .expect("message succeeds");

        assert_eq!(
            &events[3..6],
            &[
                Event::CommandOutput {
                    stream: OutputStream::Stdout,
                    chunk: "one".to_owned(),
                },
                Event::CommandOutput {
                    stream: OutputStream::Stderr,
                    chunk: "two".to_owned(),
                },
                Event::CommandOutput {
                    stream: OutputStream::Stdout,
                    chunk: "three".to_owned(),
                },
            ]
        );
    }

    #[tokio::test]
    async fn model_provider_failure_becomes_a_terminal_protocol_event() {
        let session_id = SessionId::new("session-1");
        let run_id = RunId::new("run-1");
        let model = RecordingModel {
            request: None,
            result: Err(ProviderError::new("unavailable")),
            cancel_result: Ok(true),
        };
        let mut runtime = CoreRuntime::new(model, NoopRunner);
        runtime
            .open_session(&session_id, &WorkspaceId::new("workspace-1"))
            .expect("runtime session opens");

        let events = runtime
            .handle(
                &session_id,
                Some(&run_id),
                &[],
                &Command::MessageSend {
                    content: "run".to_owned(),
                },
            )
            .await
            .expect("provider failures are normalized");

        assert_eq!(events[0], Event::RunStarted);
        assert!(matches!(
            events.last(),
            Some(Event::RunFailed { message }) if message.contains("unavailable")
        ));
    }

    #[tokio::test]
    async fn lifecycle_releases_session_state_but_keeps_workspace_long_memory() {
        let source = SessionId::new("source");
        let target = SessionId::new("target");
        let workspace_id = WorkspaceId::new("workspace-1");
        let mut runtime = CoreRuntime::new(EchoModel::default(), NoopRunner);
        runtime
            .open_session(&source, &workspace_id)
            .expect("source opens");
        runtime
            .handle(
                &source,
                None,
                &[],
                &Command::ContextUpdate {
                    path: "memory/shared".to_owned(),
                    content: "durable".to_owned(),
                },
            )
            .await
            .expect("long memory update succeeds");
        runtime
            .fork_session(&source, &target)
            .expect("target forks");

        assert_eq!(
            runtime
                .fork_session(&source, &target)
                .expect_err("existing target is rejected")
                .kind(),
            RuntimeErrorKind::TargetSessionAlreadyOpen
        );
        runtime.close_session(&source).expect("source closes");
        assert!(!runtime.is_session_open(&source));
        assert!(runtime.is_session_open(&target));
        assert_eq!(
            runtime
                .long_memory(&workspace_id)
                .expect("workspace long memory survives")
                .read("memory/shared", DisclosureLevel::Detail)
                .expect("valid path")
                .expect("entry exists")
                .content,
            "durable"
        );
        assert_eq!(
            runtime
                .handle(
                    &source,
                    None,
                    &[],
                    &Command::ContextSearch {
                        query: String::new(),
                    },
                )
                .await
                .expect_err("closed runtime session rejects work")
                .kind(),
            RuntimeErrorKind::SessionNotOpen
        );
    }

    #[tokio::test]
    async fn cancellation_requires_matching_active_run() {
        let session_id = SessionId::new("session-1");
        let run_id = RunId::new("run-1");
        let other_run_id = RunId::new("run-2");
        let mut model = RecordingModel::successful();
        model.cancel_result = Ok(false);
        let mut runtime = CoreRuntime::new(model, NoopRunner);
        runtime
            .open_session(&session_id, &WorkspaceId::new("workspace-1"))
            .expect("runtime session opens");

        assert_eq!(
            runtime
                .handle(
                    &session_id,
                    Some(&other_run_id),
                    &[],
                    &Command::RunCancel {
                        run_id: run_id.clone(),
                    },
                )
                .await
                .expect_err("mismatched run ids are rejected")
                .kind(),
            RuntimeErrorKind::MismatchedRunId
        );
        assert_eq!(
            runtime
                .handle(
                    &session_id,
                    Some(&run_id),
                    &[],
                    &Command::RunCancel {
                        run_id: run_id.clone(),
                    },
                )
                .await
                .expect_err("inactive runner run is rejected")
                .kind(),
            RuntimeErrorKind::RunNotActive
        );
    }
}
