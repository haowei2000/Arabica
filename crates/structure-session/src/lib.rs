//! Session lifecycle and scheduling.
//!
//! This module owns session/run identity, lifecycle state, dispatch, and
//! monotonically ordered event envelopes. Runtime execution remains behind
//! [`RuntimeEngine`].

use std::collections::BTreeMap;
use std::error::Error;
use std::fmt::{Display, Formatter};
use std::time::{SystemTime, UNIX_EPOCH};

use structure_protocol::{
    Command, CommandEnvelope, CommandId, ErrorCode, Event, EventEnvelope, EventId, EventMetadata,
    PROTOCOL_VERSION, RunId, RunStatus, SessionId, SessionStatus, WorkspaceId,
};
use structure_runtime::{RuntimeEngine, RuntimeEventLog};

#[derive(Clone, Debug)]
pub struct SessionRecord {
    pub id: SessionId,
    pub workspace_id: WorkspaceId,
    pub parent_session_id: Option<SessionId>,
    pub status: SessionStatus,
    pub runs: BTreeMap<RunId, RunStatus>,
    /// Immutable history inherited at the fork boundary. It is input to the
    /// short-memory projection but does not consume this Session's sequence.
    base_events: Vec<EventEnvelope>,
    /// Local append-only event log. This is the source of truth for short
    /// memory; Runtime never owns a second conversation store.
    events: Vec<EventEnvelope>,
    next_sequence: u64,
}

impl SessionRecord {
    fn runtime_history(&self) -> Vec<EventEnvelope> {
        self.base_events
            .iter()
            .chain(&self.events)
            .cloned()
            .collect()
    }

    fn append_event(
        &mut self,
        command_id: &CommandId,
        run_id: Option<&RunId>,
        event: Event,
    ) -> EventEnvelope {
        self.next_sequence += 1;
        let sequence = self.next_sequence;
        let envelope = EventEnvelope::new(
            EventMetadata {
                event_id: EventId::new(format!("event-{}-{sequence}", self.id)),
                command_id: command_id.clone(),
                workspace_id: self.workspace_id.clone(),
                session_id: self.id.clone(),
                run_id: run_id.cloned(),
                sequence,
                occurred_at_ms: unix_time_ms(),
            },
            event,
        );
        self.events.push(envelope.clone());
        envelope
    }
}

struct SessionRuntimeEventLog<'a> {
    session: &'a mut SessionRecord,
    command_id: CommandId,
    run_id: Option<RunId>,
    emitted: Vec<EventEnvelope>,
}

impl<'a> SessionRuntimeEventLog<'a> {
    fn new(session: &'a mut SessionRecord, command_id: CommandId, run_id: Option<RunId>) -> Self {
        Self {
            session,
            command_id,
            run_id,
            emitted: Vec::new(),
        }
    }

    fn into_emitted(self) -> Vec<EventEnvelope> {
        self.emitted
    }
}

impl RuntimeEventLog for SessionRuntimeEventLog<'_> {
    fn snapshot(&self) -> Vec<EventEnvelope> {
        self.session.runtime_history()
    }

    fn append(&mut self, event: Event) -> EventEnvelope {
        let client_visible = event.is_client_visible();
        let envelope = self
            .session
            .append_event(&self.command_id, self.run_id.as_ref(), event);
        if client_visible {
            self.emitted.push(envelope.clone());
        }
        envelope
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SessionError {
    pub code: ErrorCode,
    pub message: String,
}

impl SessionError {
    fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl Display for SessionError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        self.message.fmt(formatter)
    }
}

impl Error for SessionError {}

#[derive(Debug)]
pub struct SessionManager<R> {
    runtime: R,
    sessions: BTreeMap<SessionId, SessionRecord>,
    processed_commands: BTreeMap<CommandId, (CommandEnvelope, Vec<EventEnvelope>)>,
    next_session_id: u64,
    next_run_id: u64,
}

impl<R> SessionManager<R> {
    pub fn new(runtime: R) -> Self {
        Self {
            runtime,
            sessions: BTreeMap::new(),
            processed_commands: BTreeMap::new(),
            next_session_id: 1,
            next_run_id: 1,
        }
    }

    pub fn session(&self, session_id: &SessionId) -> Option<&SessionRecord> {
        self.sessions.get(session_id)
    }

    pub fn runtime(&self) -> &R {
        &self.runtime
    }

    fn allocate_session_id(&mut self) -> SessionId {
        let id = SessionId::new(format!("session-{}", self.next_session_id));
        self.next_session_id += 1;
        id
    }

    fn allocate_run_id(&mut self) -> RunId {
        let id = RunId::new(format!("run-{}", self.next_run_id));
        self.next_run_id += 1;
        id
    }

    fn require_session_id(
        &self,
        session_id: Option<&SessionId>,
    ) -> Result<SessionId, SessionError> {
        let session_id = session_id.cloned().ok_or_else(|| {
            SessionError::new(
                ErrorCode::InvalidCommand,
                "this command requires envelope.session_id",
            )
        })?;
        if !self.sessions.contains_key(&session_id) {
            return Err(SessionError::new(
                ErrorCode::SessionNotFound,
                format!("session not found: {session_id}"),
            ));
        }
        Ok(session_id)
    }

    fn require_active(&self, session_id: &SessionId) -> Result<(), SessionError> {
        let session = self.sessions.get(session_id).ok_or_else(|| {
            SessionError::new(
                ErrorCode::SessionNotFound,
                format!("session not found: {session_id}"),
            )
        })?;
        if session.status != SessionStatus::Active {
            return Err(SessionError::new(
                ErrorCode::InvalidSessionState,
                format!("session {session_id} is not active"),
            ));
        }
        Ok(())
    }

    fn append_events(
        &mut self,
        command_id: &CommandId,
        session_id: &SessionId,
        run_id: Option<&RunId>,
        events: impl IntoIterator<Item = Event>,
    ) -> Vec<EventEnvelope> {
        let session = self
            .sessions
            .get_mut(session_id)
            .expect("session must exist before events are appended");
        events
            .into_iter()
            .map(|event| session.append_event(command_id, run_id, event))
            .collect()
    }
}

impl<R: RuntimeEngine> SessionManager<R> {
    pub async fn handle(
        &mut self,
        envelope: CommandEnvelope,
    ) -> Result<Vec<EventEnvelope>, SessionError> {
        if envelope.protocol_version != PROTOCOL_VERSION {
            return Err(SessionError::new(
                ErrorCode::ProtocolVersionMismatch,
                format!(
                    "unsupported protocol version {}; expected {PROTOCOL_VERSION}",
                    envelope.protocol_version
                ),
            ));
        }

        if let Some((original, events)) = self.processed_commands.get(&envelope.command_id) {
            if original != &envelope {
                return Err(SessionError::new(
                    ErrorCode::InvalidCommand,
                    format!(
                        "command_id {} was already used with a different command",
                        envelope.command_id
                    ),
                ));
            }
            return Ok(events.clone());
        }

        let original = envelope.clone();
        let command_id = envelope.command_id.clone();
        let events = self.handle_once(envelope).await?;
        self.processed_commands
            .insert(command_id, (original, events.clone()));
        Ok(events)
    }

    async fn handle_once(
        &mut self,
        envelope: CommandEnvelope,
    ) -> Result<Vec<EventEnvelope>, SessionError> {
        let command_id = envelope.command_id;
        match envelope.command {
            Command::SessionCreate { workspace_id } => {
                let session_id = self.allocate_session_id();
                self.runtime
                    .open_session(&session_id, &workspace_id)
                    .map_err(|error| {
                        SessionError::new(ErrorCode::RuntimeFailure, error.to_string())
                    })?;
                self.sessions.insert(
                    session_id.clone(),
                    SessionRecord {
                        id: session_id.clone(),
                        workspace_id: workspace_id.clone(),
                        parent_session_id: None,
                        status: SessionStatus::Active,
                        runs: BTreeMap::new(),
                        base_events: Vec::new(),
                        events: Vec::new(),
                        next_sequence: 0,
                    },
                );
                Ok(self.append_events(
                    &command_id,
                    &session_id,
                    None,
                    [Event::SessionCreated { workspace_id }],
                ))
            }
            Command::SessionFork { source_session_id } => {
                let source = self
                    .sessions
                    .get(&source_session_id)
                    .cloned()
                    .ok_or_else(|| {
                        SessionError::new(
                            ErrorCode::SessionNotFound,
                            format!("session not found: {source_session_id}"),
                        )
                    })?;
                let inherited_events = source.runtime_history();
                let session_id = self.allocate_session_id();
                self.runtime
                    .fork_session(&source_session_id, &session_id)
                    .map_err(|error| {
                        SessionError::new(ErrorCode::RuntimeFailure, error.to_string())
                    })?;
                self.sessions.insert(
                    session_id.clone(),
                    SessionRecord {
                        id: session_id.clone(),
                        workspace_id: source.workspace_id,
                        parent_session_id: Some(source_session_id.clone()),
                        status: SessionStatus::Active,
                        runs: BTreeMap::new(),
                        base_events: inherited_events,
                        events: Vec::new(),
                        next_sequence: 0,
                    },
                );
                Ok(self.append_events(
                    &command_id,
                    &session_id,
                    None,
                    [Event::SessionForked { source_session_id }],
                ))
            }
            Command::SessionResume => {
                let session_id = self.require_session_id(envelope.session_id.as_ref())?;
                let status = self
                    .sessions
                    .get(&session_id)
                    .expect("validated session exists")
                    .status;
                if status != SessionStatus::Suspended {
                    return Err(SessionError::new(
                        ErrorCode::InvalidSessionState,
                        format!("session {session_id} cannot resume from {status:?}"),
                    ));
                }
                self.sessions
                    .get_mut(&session_id)
                    .expect("validated session exists")
                    .status = SessionStatus::Active;
                Ok(self.append_events(&command_id, &session_id, None, [Event::SessionResumed]))
            }
            Command::SessionSuspend => {
                let session_id = self.require_session_id(envelope.session_id.as_ref())?;
                self.require_active(&session_id)?;
                self.sessions
                    .get_mut(&session_id)
                    .expect("validated session exists")
                    .status = SessionStatus::Suspended;
                Ok(self.append_events(&command_id, &session_id, None, [Event::SessionSuspended]))
            }
            Command::SessionClose => {
                let session_id = self.require_session_id(envelope.session_id.as_ref())?;
                let status = self
                    .sessions
                    .get(&session_id)
                    .expect("validated session exists")
                    .status;
                if status == SessionStatus::Closed {
                    return Err(SessionError::new(
                        ErrorCode::InvalidSessionState,
                        format!("session {session_id} is already closed"),
                    ));
                }
                self.runtime.close_session(&session_id).map_err(|error| {
                    SessionError::new(ErrorCode::RuntimeFailure, error.to_string())
                })?;
                self.sessions
                    .get_mut(&session_id)
                    .expect("validated session exists")
                    .status = SessionStatus::Closed;
                Ok(self.append_events(&command_id, &session_id, None, [Event::SessionClosed]))
            }
            Command::MessageSend { content } => {
                let session_id = self.require_session_id(envelope.session_id.as_ref())?;
                self.require_active(&session_id)?;
                let run_id = self.allocate_run_id();
                self.sessions
                    .get_mut(&session_id)
                    .expect("validated session exists")
                    .runs
                    .insert(run_id.clone(), RunStatus::Pending);

                let mut envelopes = self.append_events(
                    &command_id,
                    &session_id,
                    Some(&run_id),
                    [Event::RunScheduled],
                );
                self.sessions
                    .get_mut(&session_id)
                    .expect("validated session exists")
                    .runs
                    .insert(run_id.clone(), RunStatus::Running);

                let runtime_command = Command::MessageSend { content };
                let runtime = &mut self.runtime;
                let session = self
                    .sessions
                    .get_mut(&session_id)
                    .expect("validated session exists");
                let mut event_log =
                    SessionRuntimeEventLog::new(session, command_id.clone(), Some(run_id.clone()));
                if let Err(error) = runtime
                    .handle(&session_id, Some(&run_id), &mut event_log, &runtime_command)
                    .await
                {
                    event_log.append(Event::RunFailed {
                        message: error.to_string(),
                    });
                }
                let status = terminal_run_status(&event_log.emitted).unwrap_or(RunStatus::Running);
                event_log.session.runs.insert(run_id.clone(), status);
                envelopes.extend(event_log.into_emitted());
                Ok(envelopes)
            }
            Command::RunCancel { run_id } => {
                let session_id = self.require_session_id(envelope.session_id.as_ref())?;
                let run_status = self
                    .sessions
                    .get(&session_id)
                    .expect("validated session exists")
                    .runs
                    .get(&run_id)
                    .copied()
                    .ok_or_else(|| {
                        SessionError::new(
                            ErrorCode::RunNotFound,
                            format!("run not found: {run_id}"),
                        )
                    })?;
                if !matches!(
                    run_status,
                    RunStatus::Pending | RunStatus::Running | RunStatus::WaitingForTool
                ) {
                    return Err(SessionError::new(
                        ErrorCode::InvalidSessionState,
                        format!("run {run_id} cannot be cancelled from {run_status:?}"),
                    ));
                }
                let command = Command::RunCancel {
                    run_id: run_id.clone(),
                };
                let runtime = &mut self.runtime;
                let session = self
                    .sessions
                    .get_mut(&session_id)
                    .expect("validated session exists");
                let mut event_log =
                    SessionRuntimeEventLog::new(session, command_id, Some(run_id.clone()));
                runtime
                    .handle(&session_id, Some(&run_id), &mut event_log, &command)
                    .await
                    .map_err(|error| {
                        SessionError::new(ErrorCode::RuntimeFailure, error.to_string())
                    })?;
                event_log
                    .session
                    .runs
                    .insert(run_id.clone(), RunStatus::Cancelled);
                Ok(event_log.into_emitted())
            }
            command @ (Command::ContextRead { .. }
            | Command::ContextSearch { .. }
            | Command::ContextUpdate { .. }
            | Command::ContextDelete { .. }
            | Command::ContextSetDisclosure { .. }) => {
                let session_id = self.require_session_id(envelope.session_id.as_ref())?;
                self.require_active(&session_id)?;
                let runtime = &mut self.runtime;
                let session = self
                    .sessions
                    .get_mut(&session_id)
                    .expect("validated session exists");
                let mut event_log = SessionRuntimeEventLog::new(session, command_id, None);
                if let Err(error) = runtime
                    .handle(&session_id, None, &mut event_log, &command)
                    .await
                {
                    event_log.append(Event::Error {
                        code: ErrorCode::RuntimeFailure,
                        message: error.to_string(),
                    });
                }
                Ok(event_log.into_emitted())
            }
        }
    }
}

fn unix_time_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn terminal_run_status(events: &[EventEnvelope]) -> Option<RunStatus> {
    events.iter().rev().find_map(|event| match event {
        EventEnvelope {
            event: Event::RunCompleted { .. },
            ..
        } => Some(RunStatus::Finished),
        EventEnvelope {
            event: Event::RunFailed { .. },
            ..
        } => Some(RunStatus::Failed),
        EventEnvelope {
            event: Event::RunCancelled,
            ..
        } => Some(RunStatus::Cancelled),
        _ => None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use structure_model::{FinishReason, RuntimeResponse, RuntimeUsage, ShortMemoryItem};
    use structure_protocol::ContextEntry;
    use structure_provider::{
        EchoModel, ModelProvider, ModelRunRequest, ModelRunResult, ProviderError,
    };
    use structure_runner::NoopRunner;
    use structure_runtime::CoreRuntime;

    #[derive(Debug, Default)]
    struct CapturingModel {
        requests: Vec<ModelRunRequest>,
    }

    impl ModelProvider for CapturingModel {
        async fn complete(
            &mut self,
            request: ModelRunRequest,
        ) -> Result<ModelRunResult, ProviderError> {
            let output = request.input.clone();
            self.requests.push(request);
            Ok(ModelRunResult {
                final_output: Some(output),
                prepared_request: None,
                response: None,
            })
        }

        async fn cancel(&mut self, _run_id: &RunId) -> Result<bool, ProviderError> {
            Ok(false)
        }
    }

    #[derive(Debug, Default)]
    struct TruncatedModel;

    impl ModelProvider for TruncatedModel {
        async fn complete(
            &mut self,
            _request: ModelRunRequest,
        ) -> Result<ModelRunResult, ProviderError> {
            Ok(ModelRunResult {
                final_output: None,
                prepared_request: None,
                response: Some(RuntimeResponse {
                    items: Vec::new(),
                    finish_reason: Some(FinishReason::Length),
                    usage: RuntimeUsage {
                        input_tokens: 100,
                        output_tokens: 8_192,
                        cached_input_tokens: 80,
                        cache_creation_input_tokens: 0,
                        reasoning_output_tokens: 0,
                    },
                    provider_state: None,
                }),
            })
        }

        async fn cancel(&mut self, _run_id: &RunId) -> Result<bool, ProviderError> {
            Ok(false)
        }
    }

    fn command(id: &str, session_id: Option<SessionId>, command: Command) -> CommandEnvelope {
        CommandEnvelope::new(CommandId::new(id), session_id, command)
    }

    #[tokio::test]
    async fn session_manager_schedules_runs_and_sequences_runtime_events() {
        let runtime = CoreRuntime::new(EchoModel::default(), NoopRunner);
        let mut manager = SessionManager::new(runtime);
        let created = manager
            .handle(command(
                "command-1",
                None,
                Command::SessionCreate {
                    workspace_id: WorkspaceId::new("workspace-1"),
                },
            ))
            .await
            .expect("session is created");
        let session_id = created[0].session_id.clone();

        let events = manager
            .handle(command(
                "command-2",
                Some(session_id.clone()),
                Command::MessageSend {
                    content: "hello".to_owned(),
                },
            ))
            .await
            .expect("message is dispatched");

        assert_eq!(events[0].sequence, 2);
        assert!(matches!(events[0].event, Event::RunScheduled));
        assert!(matches!(events[1].event, Event::RunStarted));
        assert!(matches!(
            events.last().expect("terminal event").event,
            Event::RunCompleted { .. }
        ));
        let run_id = events[0].run_id.as_ref().expect("run id");
        assert_eq!(
            manager
                .session(&session_id)
                .expect("session exists")
                .runs
                .get(run_id),
            Some(&RunStatus::Finished)
        );
    }

    #[tokio::test]
    async fn session_manager_marks_length_truncation_as_failed() {
        let runtime = CoreRuntime::new(TruncatedModel, NoopRunner);
        let mut manager = SessionManager::new(runtime);
        let created = manager
            .handle(command(
                "command-1",
                None,
                Command::SessionCreate {
                    workspace_id: WorkspaceId::new("workspace-1"),
                },
            ))
            .await
            .expect("session is created");
        let session_id = created[0].session_id.clone();

        let events = manager
            .handle(command(
                "command-2",
                Some(session_id.clone()),
                Command::MessageSend {
                    content: "hello".to_owned(),
                },
            ))
            .await
            .expect("truncation is represented as a terminal event");

        assert!(matches!(
            &events.last().expect("terminal event").event,
            Event::RunFailed { message } if message.starts_with("model_output_truncated:")
        ));
        let run_id = events[0].run_id.as_ref().expect("run id");
        assert_eq!(
            manager
                .session(&session_id)
                .expect("session exists")
                .runs
                .get(run_id),
            Some(&RunStatus::Failed)
        );
    }

    #[tokio::test]
    async fn forked_session_shares_workspace_long_memory() {
        let runtime = CoreRuntime::new(EchoModel::default(), NoopRunner);
        let mut manager = SessionManager::new(runtime);
        let created = manager
            .handle(command(
                "create",
                None,
                Command::SessionCreate {
                    workspace_id: WorkspaceId::new("workspace-1"),
                },
            ))
            .await
            .expect("session is created");
        let source_id = created[0].session_id.clone();
        manager
            .handle(command(
                "update",
                Some(source_id.clone()),
                Command::ContextUpdate {
                    path: "memory/name".to_owned(),
                    content: "Structure".to_owned(),
                },
            ))
            .await
            .expect("context is updated");

        let forked = manager
            .handle(command(
                "fork",
                None,
                Command::SessionFork {
                    source_session_id: source_id.clone(),
                },
            ))
            .await
            .expect("session is forked");
        let target_id = forked[0].session_id.clone();
        manager
            .handle(command(
                "update-after-fork",
                Some(source_id),
                Command::ContextUpdate {
                    path: "memory/name".to_owned(),
                    content: "Shared Structure".to_owned(),
                },
            ))
            .await
            .expect("workspace long memory is updated after fork");
        let read = manager
            .handle(command(
                "read",
                Some(target_id),
                Command::ContextRead {
                    path: "memory/name".to_owned(),
                },
            ))
            .await
            .expect("forked context can be read");

        assert_eq!(
            read[0].event,
            Event::ContextRead {
                entry: ContextEntry {
                    path: "memory/name".to_owned(),
                    content: "Shared Structure".to_owned(),
                }
            }
        );
    }

    #[tokio::test]
    async fn forked_session_inherits_short_memory_at_the_fork_boundary() {
        let runtime = CoreRuntime::new(CapturingModel::default(), NoopRunner);
        let mut manager = SessionManager::new(runtime);
        let created = manager
            .handle(command(
                "create",
                None,
                Command::SessionCreate {
                    workspace_id: WorkspaceId::new("workspace-1"),
                },
            ))
            .await
            .expect("session is created");
        let source_id = created[0].session_id.clone();
        manager
            .handle(command(
                "first-message",
                Some(source_id.clone()),
                Command::MessageSend {
                    content: "first".to_owned(),
                },
            ))
            .await
            .expect("source run completes");

        let forked = manager
            .handle(command(
                "fork",
                None,
                Command::SessionFork {
                    source_session_id: source_id,
                },
            ))
            .await
            .expect("session is forked");
        let target_id = forked[0].session_id.clone();
        manager
            .handle(command(
                "second-message",
                Some(target_id),
                Command::MessageSend {
                    content: "second".to_owned(),
                },
            ))
            .await
            .expect("forked run completes");

        let request = manager
            .runtime()
            .model()
            .requests
            .last()
            .expect("runner request exists");
        assert!(request.short_memory.iter().any(|entry| matches!(
            &entry.item,
            ShortMemoryItem::UserMessage { content } if content == "first"
        )));
        assert!(!request.short_memory.iter().any(|entry| matches!(
            &entry.item,
            ShortMemoryItem::UserMessage { content } if content == "second"
        )));
    }

    #[tokio::test]
    async fn incompatible_protocol_versions_are_rejected_at_the_boundary() {
        let runtime = CoreRuntime::new(EchoModel::default(), NoopRunner);
        let mut manager = SessionManager::new(runtime);
        let mut envelope = command(
            "create",
            None,
            Command::SessionCreate {
                workspace_id: WorkspaceId::new("workspace-1"),
            },
        );
        envelope.protocol_version = "2.0".to_owned();

        let error = manager
            .handle(envelope)
            .await
            .expect_err("version is rejected");
        assert_eq!(error.code, ErrorCode::ProtocolVersionMismatch);
    }

    #[tokio::test]
    async fn a_terminal_run_cannot_be_cancelled() {
        let runtime = CoreRuntime::new(EchoModel::default(), NoopRunner);
        let mut manager = SessionManager::new(runtime);
        let created = manager
            .handle(command(
                "create",
                None,
                Command::SessionCreate {
                    workspace_id: WorkspaceId::new("workspace-1"),
                },
            ))
            .await
            .expect("session is created");
        let session_id = created[0].session_id.clone();
        let completed = manager
            .handle(command(
                "message",
                Some(session_id.clone()),
                Command::MessageSend {
                    content: "done".to_owned(),
                },
            ))
            .await
            .expect("run completes");
        let run_id = completed[0].run_id.clone().expect("run id exists");

        let error = manager
            .handle(command(
                "cancel",
                Some(session_id),
                Command::RunCancel { run_id },
            ))
            .await
            .expect_err("terminal run cannot be cancelled");
        assert_eq!(error.code, ErrorCode::InvalidSessionState);
    }

    #[tokio::test]
    async fn retrying_a_command_id_replays_events_without_reexecuting_it() {
        let runtime = CoreRuntime::new(EchoModel::default(), NoopRunner);
        let mut manager = SessionManager::new(runtime);
        let create = command(
            "create-once",
            None,
            Command::SessionCreate {
                workspace_id: WorkspaceId::new("workspace-1"),
            },
        );

        let first = manager
            .handle(create.clone())
            .await
            .expect("first command succeeds");
        let replay = manager
            .handle(create)
            .await
            .expect("retry returns cached events");

        assert_eq!(replay, first);
        assert_eq!(manager.sessions.len(), 1);
    }

    #[tokio::test]
    async fn reusing_a_command_id_with_a_different_body_is_rejected() {
        let runtime = CoreRuntime::new(EchoModel::default(), NoopRunner);
        let mut manager = SessionManager::new(runtime);
        manager
            .handle(command(
                "same-id",
                None,
                Command::SessionCreate {
                    workspace_id: WorkspaceId::new("workspace-1"),
                },
            ))
            .await
            .expect("first command succeeds");

        let error = manager
            .handle(command(
                "same-id",
                None,
                Command::SessionCreate {
                    workspace_id: WorkspaceId::new("workspace-2"),
                },
            ))
            .await
            .expect_err("conflicting idempotency key is rejected");
        assert_eq!(error.code, ErrorCode::InvalidCommand);
    }

    #[tokio::test]
    async fn session_lifecycle_enforces_suspend_resume_and_close() {
        let runtime = CoreRuntime::new(EchoModel::default(), NoopRunner);
        let mut manager = SessionManager::new(runtime);
        let created = manager
            .handle(command(
                "create",
                None,
                Command::SessionCreate {
                    workspace_id: WorkspaceId::new("workspace-1"),
                },
            ))
            .await
            .expect("session is created");
        let session_id = created[0].session_id.clone();

        manager
            .handle(command(
                "suspend",
                Some(session_id.clone()),
                Command::SessionSuspend,
            ))
            .await
            .expect("active session suspends");
        assert_eq!(
            manager.session(&session_id).expect("session exists").status,
            SessionStatus::Suspended
        );

        manager
            .handle(command(
                "resume",
                Some(session_id.clone()),
                Command::SessionResume,
            ))
            .await
            .expect("suspended session resumes");
        manager
            .handle(command(
                "close",
                Some(session_id.clone()),
                Command::SessionClose,
            ))
            .await
            .expect("session closes");
        assert!(
            !manager.runtime().is_session_open(&session_id),
            "closing a session releases its runtime context"
        );

        let error = manager
            .handle(command(
                "message-after-close",
                Some(session_id),
                Command::MessageSend {
                    content: "blocked".to_owned(),
                },
            ))
            .await
            .expect_err("closed session rejects work");
        assert_eq!(error.code, ErrorCode::InvalidSessionState);
    }
}
