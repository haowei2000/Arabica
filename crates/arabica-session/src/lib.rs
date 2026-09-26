//! Session lifecycle and scheduling.
//!
//! This module owns session/run identity, lifecycle state, dispatch, and
//! monotonically ordered event envelopes. Runtime execution remains behind
//! [`RuntimeEngine`].

use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::fmt::{Display, Formatter};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use arabica_protocol::{
    Command, CommandEnvelope, CommandId, ErrorCode, Event, EventEnvelope, EventId, EventMetadata,
    PROTOCOL_VERSION, RunId, RunStatus, SessionId, SessionStatus, WorkspaceId,
};
use arabica_runtime::{RunControl, RuntimeEngine, RuntimeEventLog};
use serde::{Deserialize, Serialize};

/// Whether an Event reached the client-visible subset `dispatch` and `handle`
/// return, or was internal evidence such as a `model.response.item`.
///
/// This is [`Event::is_client_visible`] reified as data, so an observer that
/// wants only what a client sees does not have to duplicate that rule.
/// `Serialize`/`Deserialize` (`"client"`/`"internal"`) so a persistent
/// observer (`arabica-adapters`' file session store) can record it
/// alongside the Event it was computed for.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EventVisibility {
    Client,
    Internal,
}

/// Receives every canonical Event as it is appended, in sequence order,
/// including internal ones. This is how a host streams progress: `dispatch`
/// itself returns only after the whole Command finishes, and internal Events
/// such as `model.response.item` never appear in that return value at all.
///
/// `observe` is called synchronously, inline with the append that produced
/// the Event, from inside the `.await`ed call that is doing the work. An
/// implementation must not block: hand the envelope to a channel or a `Vec`
/// behind a mutex rather than doing I/O here.
pub trait SessionEventObserver: Send + Sync {
    fn observe(&self, envelope: &EventEnvelope, visibility: EventVisibility);
}

/// Forwards every Event to each observer in turn, in the order given, so a
/// host can attach more than one -- a persistent store (`arabica-adapters`'
/// `FileSessionStore`) alongside a live-progress observer, say -- to one
/// `dispatch` call. [`DispatchControl`] only carries one
/// `Arc<dyn SessionEventObserver>`; this is how a host composes several into
/// that one slot instead of `arabica-session` growing a second observer
/// field for every combination a host might want.
pub struct FanOutObserver(Vec<Arc<dyn SessionEventObserver>>);

impl FanOutObserver {
    pub fn new(observers: Vec<Arc<dyn SessionEventObserver>>) -> Self {
        Self(observers)
    }
}

impl SessionEventObserver for FanOutObserver {
    fn observe(&self, envelope: &EventEnvelope, visibility: EventVisibility) {
        for observer in &self.0 {
            observer.observe(envelope, visibility);
        }
    }
}

/// Everything a host may attach to one call to [`SessionManager::dispatch`].
///
/// The default attaches nothing: no cancellation or permission gate reaches
/// Runtime, and no observer is notified. A Command dispatched with the
/// default emits exactly the Events it emitted before this type existed;
/// [`SessionManager::handle`] uses the default so its behavior is unchanged.
#[derive(Clone, Default)]
pub struct DispatchControl {
    pub run: RunControl,
    pub observer: Option<Arc<dyn SessionEventObserver>>,
}

/// Allocates Session and Run identifiers.
///
/// The default, [`SequentialIds`], reproduces the `session-N` / `run-N`
/// format every existing caller and recorded benchmark campaign depends on.
/// A host that runs one `SessionManager` per external session (for example
/// one per ACP connection) needs identifiers unique across managers, not just
/// within one, and supplies its own allocator (a UUIDv7 generator, typically)
/// through [`SessionManager::with_ids`].
pub trait IdAllocator: Send + std::fmt::Debug {
    fn session_id(&mut self) -> SessionId;
    fn run_id(&mut self) -> RunId;

    /// Tells the allocator that `session_id` is already in use, by a session
    /// [`SessionManager::restore_session`] just reconstructed, so a later
    /// [`Self::session_id`] call must never produce it. The default is a
    /// no-op, which suits an allocator (a UUIDv7 generator, say) whose
    /// collision probability is already negligible regardless of prior
    /// history; a counter-based allocator overrides it to advance past the
    /// observed id.
    fn observe_used_session_id(&mut self, session_id: &SessionId) {
        let _ = session_id;
    }

    /// The [`Self::run_id`] counterpart of [`Self::observe_used_session_id`].
    /// A restored session's history can contain many run ids (one per past
    /// turn), so this is called once per id found, not once per restore.
    fn observe_used_run_id(&mut self, run_id: &RunId) {
        let _ = run_id;
    }
}

#[derive(Debug, Default)]
pub struct SequentialIds {
    next_session_id: u64,
    next_run_id: u64,
}

/// Parses the `N` out of a `SequentialIds`-shaped id (`"session-N"` or
/// `"run-N"`); anything else (a UUID, say) parses to `None` and is silently
/// ignored, since `observe_used_*`'s only job is to keep this specific
/// counter-based scheme from reissuing an id already seen, not to make sense
/// of ids some other allocator minted.
fn sequential_suffix(id: &str, prefix: &str) -> Option<u64> {
    id.strip_prefix(prefix)?.parse().ok()
}

impl IdAllocator for SequentialIds {
    fn session_id(&mut self) -> SessionId {
        self.next_session_id += 1;
        SessionId::new(format!("session-{}", self.next_session_id))
    }

    fn run_id(&mut self) -> RunId {
        self.next_run_id += 1;
        RunId::new(format!("run-{}", self.next_run_id))
    }

    fn observe_used_session_id(&mut self, session_id: &SessionId) {
        if let Some(n) = sequential_suffix(&session_id.0, "session-") {
            self.next_session_id = self.next_session_id.max(n);
        }
    }

    fn observe_used_run_id(&mut self, run_id: &RunId) {
        if let Some(n) = sequential_suffix(&run_id.0, "run-") {
            self.next_run_id = self.next_run_id.max(n);
        }
    }
}

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

    /// Append one canonical Event and, if a host attached one, notify the
    /// observer. This is the single place envelopes are constructed, so both
    /// runtime-driven Events (through `SessionRuntimeEventLog`) and
    /// session-lifecycle Events (through `SessionManager::append_events`)
    /// reach the same observer through the same path.
    fn append_event(
        &mut self,
        command_id: &CommandId,
        run_id: Option<&RunId>,
        event: Event,
        observer: Option<&dyn SessionEventObserver>,
    ) -> EventEnvelope {
        self.next_sequence += 1;
        let sequence = self.next_sequence;
        let visibility = if event.is_client_visible() {
            EventVisibility::Client
        } else {
            EventVisibility::Internal
        };
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
        if let Some(observer) = observer {
            observer.observe(&envelope, visibility);
        }
        envelope
    }
}

struct SessionRuntimeEventLog<'a> {
    session: &'a mut SessionRecord,
    command_id: CommandId,
    run_id: Option<RunId>,
    observer: Option<&'a dyn SessionEventObserver>,
    emitted: Vec<EventEnvelope>,
}

impl<'a> SessionRuntimeEventLog<'a> {
    fn new(
        session: &'a mut SessionRecord,
        command_id: CommandId,
        run_id: Option<RunId>,
        observer: Option<&'a dyn SessionEventObserver>,
    ) -> Self {
        Self {
            session,
            command_id,
            run_id,
            observer,
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
        let envelope =
            self.session
                .append_event(&self.command_id, self.run_id.as_ref(), event, self.observer);
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
    ids: Box<dyn IdAllocator>,
}

impl<R> SessionManager<R> {
    pub fn new(runtime: R) -> Self {
        Self::with_ids(runtime, Box::new(SequentialIds::default()))
    }

    /// Construct with a caller-supplied [`IdAllocator`], for a host that runs
    /// one `SessionManager` per external session and needs identifiers
    /// unique across managers, not just within one.
    pub fn with_ids(runtime: R, ids: Box<dyn IdAllocator>) -> Self {
        Self {
            runtime,
            sessions: BTreeMap::new(),
            processed_commands: BTreeMap::new(),
            ids,
        }
    }

    pub fn session(&self, session_id: &SessionId) -> Option<&SessionRecord> {
        self.sessions.get(session_id)
    }

    pub fn runtime(&self) -> &R {
        &self.runtime
    }

    pub fn runtime_mut(&mut self) -> &mut R {
        &mut self.runtime
    }

    fn allocate_session_id(&mut self) -> SessionId {
        self.ids.session_id()
    }

    fn allocate_run_id(&mut self) -> RunId {
        self.ids.run_id()
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
        observer: Option<&dyn SessionEventObserver>,
    ) -> Vec<EventEnvelope> {
        let session = self
            .sessions
            .get_mut(session_id)
            .expect("session must exist before events are appended");
        events
            .into_iter()
            .map(|event| session.append_event(command_id, run_id, event, observer))
            .collect()
    }
}

impl<R: RuntimeEngine> SessionManager<R> {
    /// Handle one Command with no host control attached: no cancellation or
    /// permission gate reaches Runtime, and nothing observes Events as they
    /// occur. Delegates to [`Self::dispatch`] with the default
    /// [`DispatchControl`], so its behavior is exactly what it was before
    /// `dispatch` existed.
    pub async fn handle(
        &mut self,
        envelope: CommandEnvelope,
    ) -> Result<Vec<EventEnvelope>, SessionError> {
        self.dispatch(envelope, DispatchControl::default()).await
    }

    /// Handle one Command with host-supplied control for the run it starts
    /// and an observer notified of every Event as it is appended, including
    /// internal ones. Still returns only the client-visible subset, exactly
    /// like [`Self::handle`]; the observer is the channel for everything
    /// else.
    ///
    /// A retried `command_id` (see the idempotency rule below) is replayed
    /// from the cached result without re-invoking Runtime, so the observer is
    /// not notified a second time for it: nothing new was appended.
    pub async fn dispatch(
        &mut self,
        envelope: CommandEnvelope,
        control: DispatchControl,
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
        let events = self.handle_once(envelope, &control).await?;
        self.processed_commands
            .insert(command_id, (original, events.clone()));
        Ok(events)
    }

    async fn handle_once(
        &mut self,
        envelope: CommandEnvelope,
        control: &DispatchControl,
    ) -> Result<Vec<EventEnvelope>, SessionError> {
        let observer = control.observer.as_deref();
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
                    observer,
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
                    observer,
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
                Ok(self.append_events(
                    &command_id,
                    &session_id,
                    None,
                    [Event::SessionResumed],
                    observer,
                ))
            }
            Command::SessionSuspend => {
                let session_id = self.require_session_id(envelope.session_id.as_ref())?;
                self.require_active(&session_id)?;
                self.sessions
                    .get_mut(&session_id)
                    .expect("validated session exists")
                    .status = SessionStatus::Suspended;
                Ok(self.append_events(
                    &command_id,
                    &session_id,
                    None,
                    [Event::SessionSuspended],
                    observer,
                ))
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
                Ok(self.append_events(
                    &command_id,
                    &session_id,
                    None,
                    [Event::SessionClosed],
                    observer,
                ))
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
                    observer,
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
                let mut event_log = SessionRuntimeEventLog::new(
                    session,
                    command_id.clone(),
                    Some(run_id.clone()),
                    observer,
                );
                if let Err(error) = runtime
                    .handle_with_control(
                        &session_id,
                        Some(&run_id),
                        &mut event_log,
                        &runtime_command,
                        &control.run,
                    )
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
                let mut event_log = SessionRuntimeEventLog::new(
                    session,
                    command_id,
                    Some(run_id.clone()),
                    observer,
                );
                runtime
                    .handle_with_control(
                        &session_id,
                        Some(&run_id),
                        &mut event_log,
                        &command,
                        &control.run,
                    )
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
                let mut event_log =
                    SessionRuntimeEventLog::new(session, command_id, None, observer);
                if let Err(error) = runtime
                    .handle_with_control(&session_id, None, &mut event_log, &command, &control.run)
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

    /// Reconstructs a [`SessionRecord`] from a session's replayed Event
    /// history and re-establishes the Runtime-side state
    /// [`RuntimeEngine::restore_session`] owns.
    ///
    /// Rejects a snapshot whose Events do not form a session this can
    /// reconstruct: empty, non-contiguous, or forked (Phase 2 does not
    /// restore a session with a parent -- its `base_events` would need the
    /// parent's own history too, which this snapshot alone does not carry).
    /// Also rejects restoring over a session id that is already open,
    /// matching `Command::SessionCreate`'s own collision behavior.
    ///
    /// A run left without a terminal Event (the process ended mid-run) is
    /// repaired before this returns: every dangling tool call -- requested
    /// but never completed -- gets a synthetic `tool.call.completed` with an
    /// error first, then the run itself gets `run.failed`. Both are
    /// appended under `restore_command_id` through the same
    /// observer-notifying path as any live Event, so a reattached
    /// persistent store sees exactly what happened: the run did not
    /// finish, here is why.
    pub fn restore_session(
        &mut self,
        snapshot: SessionSnapshot,
        restore_command_id: CommandId,
        observer: Option<&dyn SessionEventObserver>,
    ) -> Result<RestoreReport, SessionError> {
        let events = snapshot.events;
        let Some(first) = events.first() else {
            return Err(SessionError::new(
                ErrorCode::InvalidSnapshot,
                "a session snapshot must contain at least one Event",
            ));
        };
        if !matches!(first.event, Event::SessionCreated { .. }) {
            return Err(SessionError::new(
                ErrorCode::InvalidSnapshot,
                "restore only accepts a session with no parent; its first Event must be session.created",
            ));
        }
        validate_contiguous_sequence(&events)?;

        let session_id = first.session_id.clone();
        let workspace_id = first.workspace_id.clone();
        if self.sessions.contains_key(&session_id) {
            return Err(SessionError::new(
                ErrorCode::InvalidSessionState,
                format!("session {session_id} is already open; cannot restore over it"),
            ));
        }

        let status = derive_status(&events);
        let (runs, interrupted_runs) = derive_runs(&events);
        let next_sequence = events.last().expect("checked non-empty above").sequence;
        let dangling_by_run: Vec<(RunId, Vec<(String, String)>)> = interrupted_runs
            .iter()
            .map(|run_id| {
                let run_events: Vec<EventEnvelope> = events
                    .iter()
                    .filter(|envelope| envelope.run_id.as_ref() == Some(run_id))
                    .cloned()
                    .collect();
                (run_id.clone(), dangling_tool_calls(&run_events))
            })
            .collect();

        self.runtime
            .restore_session(&session_id, &workspace_id, &events)
            .map_err(|error| SessionError::new(ErrorCode::RuntimeFailure, error.to_string()))?;

        let restored_event_count = events.len();
        self.sessions.insert(
            session_id.clone(),
            SessionRecord {
                id: session_id.clone(),
                workspace_id,
                parent_session_id: None,
                status,
                runs,
                base_events: Vec::new(),
                events,
                next_sequence,
            },
        );

        let mut repaired_events = Vec::new();
        for (run_id, dangling) in dangling_by_run {
            for (call_id, name) in dangling {
                repaired_events.extend(
                    self.append_events(
                        &restore_command_id,
                        &session_id,
                        Some(&run_id),
                        [Event::ToolCallCompleted {
                            call_id,
                            name,
                            result: "run_interrupted: the process ended before this call completed"
                                .to_owned(),
                            is_error: true,
                        }],
                        observer,
                    ),
                );
            }
            repaired_events.extend(self.append_events(
                &restore_command_id,
                &session_id,
                Some(&run_id),
                [Event::RunFailed {
                    message: format!(
                        "run_interrupted: the process ended before run {run_id} finished"
                    ),
                }],
                observer,
            ));
        }

        self.ids.observe_used_session_id(&session_id);
        for run_id in self
            .sessions
            .get(&session_id)
            .expect("just inserted")
            .runs
            .keys()
        {
            self.ids.observe_used_run_id(run_id);
        }

        Ok(RestoreReport {
            session_id,
            restored_event_count,
            repaired_events,
            interrupted_runs,
        })
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

/// A session's replayed Event history, ready for
/// [`SessionManager::restore_session`]. Typically read back from a
/// `arabica-adapters` `FileSessionStore` file, but this type carries no
/// file-format knowledge itself -- restoring only needs the Events in
/// order, from wherever a caller got them.
pub struct SessionSnapshot {
    pub events: Vec<EventEnvelope>,
}

/// What `restore_session` actually did, for a caller that wants to log or
/// display it (a CLI's `--resume` telling the user "recovered N Events,
/// repaired an interrupted run", say) rather than re-deriving it from the
/// session afterward.
#[derive(Debug)]
pub struct RestoreReport {
    pub session_id: SessionId,
    pub restored_event_count: usize,
    /// The synthetic Events `restore_session` itself appended to repair an
    /// interrupted run (dangling `tool.call.completed`s, then
    /// `run.failed`), in the order they were appended. Empty when nothing
    /// needed repair.
    pub repaired_events: Vec<EventEnvelope>,
    /// Which runs, if any, had no terminal Event in the snapshot and were
    /// therefore repaired and marked `RunStatus::Failed`.
    pub interrupted_runs: Vec<RunId>,
}

fn validate_contiguous_sequence(events: &[EventEnvelope]) -> Result<(), SessionError> {
    for (index, envelope) in events.iter().enumerate() {
        let expected = (index as u64) + 1;
        if envelope.sequence != expected {
            return Err(SessionError::new(
                ErrorCode::InvalidSnapshot,
                format!(
                    "non-contiguous snapshot: expected sequence {expected} at position {index}, found {}",
                    envelope.sequence
                ),
            ));
        }
    }
    Ok(())
}

fn derive_status(events: &[EventEnvelope]) -> SessionStatus {
    events
        .iter()
        .rev()
        .find_map(|envelope| match &envelope.event {
            Event::SessionCreated { .. } | Event::SessionForked { .. } | Event::SessionResumed => {
                Some(SessionStatus::Active)
            }
            Event::SessionSuspended => Some(SessionStatus::Suspended),
            Event::SessionClosed => Some(SessionStatus::Closed),
            _ => None,
        })
        // Unreachable in practice: `restore_session` already rejects a
        // snapshot whose first Event is not `session.created`, so at least
        // one lifecycle Event always exists. Active is the safe fallback
        // regardless, since anything else would refuse an operation the
        // Runtime state might actually support.
        .unwrap_or(SessionStatus::Active)
}

/// Every run named by a `run_id` in `events`, with the status its own
/// terminal Event implies -- or, for a run with none (the process ended
/// before it finished), `RunStatus::Failed`, since `restore_session`
/// repairs every such run into that state before returning. Also returns
/// which run ids those were, so the repair step knows which ones to act on.
fn derive_runs(events: &[EventEnvelope]) -> (BTreeMap<RunId, RunStatus>, Vec<RunId>) {
    let mut runs = BTreeMap::new();
    let mut interrupted = Vec::new();
    let run_ids: BTreeSet<RunId> = events.iter().filter_map(|e| e.run_id.clone()).collect();
    for run_id in run_ids {
        let run_events: Vec<EventEnvelope> = events
            .iter()
            .filter(|envelope| envelope.run_id.as_ref() == Some(&run_id))
            .cloned()
            .collect();
        match terminal_run_status(&run_events) {
            Some(status) => {
                runs.insert(run_id, status);
            }
            None => {
                runs.insert(run_id.clone(), RunStatus::Failed);
                interrupted.push(run_id);
            }
        }
    }
    (runs, interrupted)
}

/// `(call_id, name)` for every `tool.call.requested` in `run_events` that
/// has no matching `tool.call.completed`, in request order.
fn dangling_tool_calls(run_events: &[EventEnvelope]) -> Vec<(String, String)> {
    let mut requested = Vec::new();
    let mut completed = BTreeSet::new();
    for envelope in run_events {
        match &envelope.event {
            Event::ToolCallRequested { call_id, name, .. } => {
                requested.push((call_id.clone(), name.clone()));
            }
            Event::ToolCallCompleted { call_id, .. } => {
                completed.insert(call_id.clone());
            }
            _ => {}
        }
    }
    requested
        .into_iter()
        .filter(|(call_id, _)| !completed.contains(call_id))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use arabica_model::{
        ContentBlock, MessageItem, RuntimeItem, RuntimeRole, ToolCallItem, ToolResultItem,
    };
    use arabica_model::{FinishReason, RuntimeResponse, RuntimeUsage, ShortMemoryItem};
    use arabica_protocol::ContextEntry;
    use arabica_provider::{
        EchoModel, ModelProvider, ModelRunRequest, ModelRunResult, ProviderError,
    };
    use arabica_runner::NoopRunner;
    use arabica_runner::{
        RunnerEnvironment, RunnerError, RunnerOutput, ToolExecutionRequest, ToolExecutionResult,
    };
    use arabica_runtime::{
        CoreRuntime, RunCancellation, ToolPermissionGate, ToolPermissionPolicy, ToolPermissionRule,
    };
    use std::sync::Mutex;

    #[test]
    fn event_visibility_serializes_as_the_documented_lowercase_strings() {
        assert_eq!(
            serde_json::to_string(&EventVisibility::Client).unwrap(),
            "\"client\""
        );
        assert_eq!(
            serde_json::to_string(&EventVisibility::Internal).unwrap(),
            "\"internal\""
        );
        assert_eq!(
            serde_json::from_str::<EventVisibility>("\"client\"").unwrap(),
            EventVisibility::Client
        );
    }

    #[test]
    fn fan_out_observer_forwards_to_every_observer_in_order() {
        struct Named(&'static str, Arc<Mutex<Vec<&'static str>>>);
        impl SessionEventObserver for Named {
            fn observe(&self, _envelope: &EventEnvelope, _visibility: EventVisibility) {
                self.1.lock().unwrap().push(self.0);
            }
        }

        let log: Arc<Mutex<Vec<&'static str>>> = Arc::new(Mutex::new(Vec::new()));
        let fan_out = FanOutObserver::new(vec![
            Arc::new(Named("first", log.clone())),
            Arc::new(Named("second", log.clone())),
        ]);

        let envelope = EventEnvelope::new(
            EventMetadata {
                event_id: EventId::new("event-1"),
                command_id: CommandId::new("command-1"),
                workspace_id: WorkspaceId::new("ws-1"),
                session_id: SessionId::new("session-1"),
                run_id: None,
                sequence: 1,
                occurred_at_ms: 0,
            },
            Event::RunScheduled,
        );
        fan_out.observe(&envelope, EventVisibility::Client);

        assert_eq!(*log.lock().unwrap(), vec!["first", "second"]);
    }

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

    /// A model that emits a real `model.response.item` (an internal Event)
    /// in addition to `final_output`, so a run through it produces at least
    /// one internal Event alongside its client-visible ones.
    #[derive(Debug, Default)]
    struct TextItemModel;

    impl ModelProvider for TextItemModel {
        async fn complete(
            &mut self,
            request: ModelRunRequest,
        ) -> Result<ModelRunResult, ProviderError> {
            let output = format!("echo: {}", request.input);
            Ok(ModelRunResult {
                final_output: Some(output.clone()),
                prepared_request: None,
                response: Some(RuntimeResponse {
                    items: vec![RuntimeItem::Message(MessageItem::text(
                        RuntimeRole::Assistant,
                        output,
                    ))],
                    finish_reason: Some(FinishReason::Stop),
                    usage: RuntimeUsage::default(),
                    provider_state: None,
                }),
            })
        }

        async fn cancel(&mut self, _run_id: &RunId) -> Result<bool, ProviderError> {
            Ok(false)
        }
    }

    /// Records every envelope and its visibility, in the order `observe` is
    /// called. `observe` takes `&self`, so a std `Mutex` provides the
    /// interior mutability; no `.await` is ever held across the lock.
    #[derive(Debug, Default)]
    struct RecordingObserver(Mutex<Vec<(EventEnvelope, EventVisibility)>>);

    impl RecordingObserver {
        fn seen(&self) -> Vec<(EventEnvelope, EventVisibility)> {
            self.0
                .lock()
                .expect("observer mutex is not poisoned")
                .clone()
        }
    }

    impl SessionEventObserver for RecordingObserver {
        fn observe(&self, envelope: &EventEnvelope, visibility: EventVisibility) {
            self.0
                .lock()
                .expect("observer mutex is not poisoned")
                .push((envelope.clone(), visibility));
        }
    }

    #[tokio::test]
    async fn dispatch_with_the_default_control_matches_handle_exactly() {
        // The default DispatchControl must be behaviorally invisible: this is
        // what lets `handle()` delegate to `dispatch()` without changing what
        // every existing caller and recorded benchmark campaign observes.
        let mut plain = SessionManager::new(CoreRuntime::new(EchoModel::default(), NoopRunner));
        let created = plain
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
        let expected = plain
            .handle(command(
                "message",
                Some(session_id.clone()),
                Command::MessageSend {
                    content: "hello".to_owned(),
                },
            ))
            .await
            .expect("message is handled");

        let mut controlled =
            SessionManager::new(CoreRuntime::new(EchoModel::default(), NoopRunner));
        controlled
            .dispatch(
                command(
                    "create",
                    None,
                    Command::SessionCreate {
                        workspace_id: WorkspaceId::new("workspace-1"),
                    },
                ),
                DispatchControl::default(),
            )
            .await
            .expect("session is created");
        let events = controlled
            .dispatch(
                command(
                    "message",
                    Some(session_id),
                    Command::MessageSend {
                        content: "hello".to_owned(),
                    },
                ),
                DispatchControl::default(),
            )
            .await
            .expect("message is handled");

        // occurred_at_ms is wall-clock and explicitly informational (see
        // docs/protocol.md: consumers order by sequence, never by it), so two
        // separately timed runs may legitimately differ there by a
        // millisecond. The claim under test is that dispatch with the
        // default control changes none of the canonical content.
        let content = |envelopes: &[EventEnvelope]| {
            envelopes
                .iter()
                .map(|envelope| envelope.event.clone())
                .collect::<Vec<_>>()
        };
        assert_eq!(content(&events), content(&expected));
    }

    #[tokio::test]
    async fn the_observer_sees_every_event_including_internal_ones_in_order() {
        let observer: Arc<RecordingObserver> = Arc::default();
        let mut manager = SessionManager::new(CoreRuntime::new(TextItemModel, NoopRunner));

        let created = manager
            .dispatch(
                command(
                    "create",
                    None,
                    Command::SessionCreate {
                        workspace_id: WorkspaceId::new("workspace-1"),
                    },
                ),
                DispatchControl {
                    run: RunControl::default(),
                    observer: Some(observer.clone()),
                },
            )
            .await
            .expect("session is created");
        let session_id = created[0].session_id.clone();

        let returned = manager
            .dispatch(
                command(
                    "message",
                    Some(session_id),
                    Command::MessageSend {
                        content: "hi".to_owned(),
                    },
                ),
                DispatchControl {
                    run: RunControl::default(),
                    observer: Some(observer.clone()),
                },
            )
            .await
            .expect("message is handled");

        let seen = observer.seen();

        // Sequence order is preserved end to end.
        let sequences: Vec<u64> = seen.iter().map(|(envelope, _)| envelope.sequence).collect();
        let mut sorted = sequences.clone();
        sorted.sort_unstable();
        assert_eq!(
            sequences, sorted,
            "observed order must match sequence order"
        );

        // session.created is a lifecycle Event appended through append_events,
        // not through SessionRuntimeEventLog; the observer must see it too.
        assert!(seen.iter().any(|(envelope, visibility)| matches!(
            envelope.event,
            Event::SessionCreated { .. }
        ) && *visibility
            == EventVisibility::Client));

        // model.response.item is internal: never in dispatch's own return
        // value, but the observer must see it, and label it Internal.
        assert!(
            !returned
                .iter()
                .any(|envelope| matches!(envelope.event, Event::ModelResponseItem { .. })),
            "model.response.item must not be client-visible"
        );
        assert!(seen.iter().any(|(envelope, visibility)| matches!(
            envelope.event,
            Event::ModelResponseItem { .. }
        ) && *visibility
            == EventVisibility::Internal));

        // Every Event dispatch returned was also observed, labeled Client.
        for envelope in &returned {
            assert!(
                seen.iter()
                    .any(
                        |(seen_envelope, visibility)| seen_envelope.event_id == envelope.event_id
                            && *visibility == EventVisibility::Client
                    ),
                "returned event {:?} was not observed as Client-visible",
                envelope.event_id
            );
        }
    }

    /// Hangs until cancelled, so a run through it only ends if cancellation
    /// actually reaches Runtime through `dispatch`.
    #[derive(Debug, Default)]
    struct HangingModel;

    impl ModelProvider for HangingModel {
        async fn complete(
            &mut self,
            _request: ModelRunRequest,
        ) -> Result<ModelRunResult, ProviderError> {
            std::future::pending().await
        }

        async fn cancel(&mut self, _run_id: &RunId) -> Result<bool, ProviderError> {
            Ok(true)
        }
    }

    #[tokio::test]
    async fn dispatch_propagates_cancellation_to_a_hanging_run() {
        let mut manager = SessionManager::new(CoreRuntime::new(HangingModel, NoopRunner));
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

        let cancellation = RunCancellation::new();
        let control = DispatchControl {
            run: RunControl {
                cancellation: Some(cancellation.clone()),
                permissions: None,
            },
            observer: None,
        };
        let run = manager.dispatch(
            command(
                "message",
                Some(session_id),
                Command::MessageSend {
                    content: "hang".to_owned(),
                },
            ),
            control,
        );
        let cancel_after_a_moment = async {
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
            cancellation.cancel();
        };
        let (result, ()) = tokio::time::timeout(std::time::Duration::from_secs(5), async {
            tokio::join!(run, cancel_after_a_moment)
        })
        .await
        .expect("dispatch must end once cancelled, not hang");

        let events = result.expect("a cancelled run is still a handled Command");
        assert!(matches!(
            events.last(),
            Some(EventEnvelope {
                event: Event::RunCancelled,
                ..
            })
        ));
    }

    /// Denies every call, so a permission gate threaded through `dispatch`
    /// must be what stops the tool from ever reaching this runner.
    #[derive(Debug, Default)]
    struct CountingRunner {
        executed: Vec<String>,
    }

    impl RunnerEnvironment for CountingRunner {
        async fn execute(
            &mut self,
            request: ToolExecutionRequest,
        ) -> Result<ToolExecutionResult, RunnerError> {
            self.executed.push(request.call.call_id.clone());
            Ok(ToolExecutionResult {
                result: ToolResultItem {
                    id: None,
                    call_id: request.call.call_id,
                    name: Some(request.call.name),
                    content: vec![ContentBlock::text("done")],
                    is_error: false,
                },
                output: vec![RunnerOutput::Stdout("done".to_owned())],
            })
        }

        async fn cancel(&mut self, _run_id: &RunId) -> Result<bool, RunnerError> {
            Ok(false)
        }
    }

    #[derive(Debug, Default)]
    struct OneToolCallModel {
        called: bool,
    }

    impl ModelProvider for OneToolCallModel {
        async fn complete(
            &mut self,
            _request: ModelRunRequest,
        ) -> Result<ModelRunResult, ProviderError> {
            if self.called {
                return Ok(ModelRunResult {
                    final_output: Some("done".to_owned()),
                    prepared_request: None,
                    response: None,
                });
            }
            self.called = true;
            Ok(ModelRunResult {
                final_output: None,
                prepared_request: None,
                response: Some(RuntimeResponse {
                    items: vec![RuntimeItem::ToolCall(ToolCallItem {
                        id: None,
                        call_id: "call-1".to_owned(),
                        name: "shell".to_owned(),
                        arguments: serde_json::json!({}),
                        provider_state: None,
                    })],
                    finish_reason: Some(FinishReason::ToolCalls),
                    usage: RuntimeUsage::default(),
                    provider_state: None,
                }),
            })
        }

        async fn cancel(&mut self, _run_id: &RunId) -> Result<bool, ProviderError> {
            Ok(false)
        }
    }

    #[tokio::test]
    async fn dispatch_propagates_the_permission_gate_and_denies_the_call() {
        let mut manager = SessionManager::new(CoreRuntime::new(
            OneToolCallModel::default(),
            CountingRunner::default(),
        ));
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

        let mut by_tool = std::collections::BTreeMap::new();
        by_tool.insert("shell".to_owned(), ToolPermissionRule::Deny);
        let control = DispatchControl {
            run: RunControl {
                cancellation: None,
                permissions: Some(ToolPermissionGate {
                    policy: ToolPermissionPolicy {
                        default: ToolPermissionRule::Allow,
                        by_tool,
                    },
                    approver: None,
                }),
            },
            observer: None,
        };
        let events = manager
            .dispatch(
                command(
                    "message",
                    Some(session_id),
                    Command::MessageSend {
                        content: "run shell".to_owned(),
                    },
                ),
                control,
            )
            .await
            .expect("the run is handled");

        assert!(
            events.iter().any(|envelope| matches!(
                &envelope.event,
                Event::ToolCallPermissionResolved {
                    outcome: arabica_protocol::ToolPermissionOutcome::Denied,
                    ..
                }
            )),
            "the gate's denial must reach dispatch's own return value: {events:?}"
        );
        assert!(
            manager.runtime().runner().executed.is_empty(),
            "a denied call must never reach the runner"
        );
    }

    #[test]
    fn a_custom_id_allocator_actually_changes_the_produced_identifiers() {
        #[derive(Debug, Default)]
        struct FixedIds(u64);
        impl IdAllocator for FixedIds {
            fn session_id(&mut self) -> SessionId {
                self.0 += 1;
                SessionId::new(format!("s-{}", self.0))
            }
            fn run_id(&mut self) -> RunId {
                self.0 += 1;
                RunId::new(format!("r-{}", self.0))
            }
        }
        let mut manager = SessionManager::with_ids(
            CoreRuntime::new(EchoModel::default(), NoopRunner),
            Box::new(FixedIds::default()),
        );
        let session_id = manager.allocate_session_id();
        let run_id = manager.allocate_run_id();
        assert_eq!(session_id, SessionId::new("s-1"));
        assert_eq!(run_id, RunId::new("r-2"));
    }

    #[test]
    fn sequential_ids_observing_a_used_id_advances_past_it() {
        let mut ids = SequentialIds::default();
        ids.observe_used_session_id(&SessionId::new("session-7"));
        ids.observe_used_run_id(&RunId::new("run-3"));
        assert_eq!(ids.session_id(), SessionId::new("session-8"));
        assert_eq!(ids.run_id(), RunId::new("run-4"));
    }

    #[test]
    fn sequential_ids_observing_a_lower_id_than_already_issued_does_not_rewind() {
        let mut ids = SequentialIds::default();
        assert_eq!(ids.session_id(), SessionId::new("session-1"));
        assert_eq!(ids.session_id(), SessionId::new("session-2"));
        // A restore observing an older, smaller id than the allocator has
        // already handed out live must not walk the counter backwards and
        // risk a future collision with an id already issued this run.
        ids.observe_used_session_id(&SessionId::new("session-1"));
        assert_eq!(ids.session_id(), SessionId::new("session-3"));
    }

    #[test]
    fn sequential_ids_ignores_an_id_it_would_never_have_produced_itself() {
        let mut ids = SequentialIds::default();
        ids.observe_used_run_id(&RunId::new("01a0c7ea-4f84-73fb-a00d-26087771166a"));
        ids.observe_used_run_id(&RunId::new("not-even-close"));
        assert_eq!(
            ids.run_id(),
            RunId::new("run-1"),
            "a foreign id shape must not perturb this allocator's own counter"
        );
    }

    mod restore_session_tests {
        use super::*;

        fn snap_event(sequence: u64, run_id: Option<&str>, event: Event) -> EventEnvelope {
            EventEnvelope {
                protocol_version: PROTOCOL_VERSION.to_owned(),
                event_id: EventId::new(format!("event-{sequence}")),
                command_id: CommandId::new("original-command"),
                workspace_id: WorkspaceId::new("ws-1"),
                session_id: SessionId::new("restored-session"),
                run_id: run_id.map(RunId::new),
                sequence,
                occurred_at_ms: 0,
                event,
            }
        }

        fn manager() -> SessionManager<CoreRuntime<EchoModel, NoopRunner>> {
            SessionManager::new(CoreRuntime::new(EchoModel::default(), NoopRunner))
        }

        /// A well-formed, fully-terminated one-run history: created, one
        /// completed run with a read tool call, nothing left dangling.
        fn healthy_history() -> Vec<EventEnvelope> {
            vec![
                snap_event(
                    1,
                    None,
                    Event::SessionCreated {
                        workspace_id: WorkspaceId::new("ws-1"),
                    },
                ),
                snap_event(2, Some("run-1"), Event::RunScheduled),
                snap_event(3, Some("run-1"), Event::RunStarted),
                snap_event(
                    4,
                    Some("run-1"),
                    Event::ToolCallRequested {
                        call_id: "call-1".to_owned(),
                        name: "read_file".to_owned(),
                        arguments: serde_json::json!({"path": "a.txt"}),
                        provider_state: None,
                    },
                ),
                snap_event(
                    5,
                    Some("run-1"),
                    Event::ToolCallCompleted {
                        call_id: "call-1".to_owned(),
                        name: "read_file".to_owned(),
                        result: "contents".to_owned(),
                        is_error: false,
                    },
                ),
                snap_event(
                    6,
                    Some("run-1"),
                    Event::RunCompleted {
                        output: Some("done".to_owned()),
                    },
                ),
            ]
        }

        #[test]
        fn restores_a_healthy_session_exactly() {
            let mut sessions = manager();
            let report = sessions
                .restore_session(
                    SessionSnapshot {
                        events: healthy_history(),
                    },
                    CommandId::new("restore-1"),
                    None,
                )
                .expect("a well-formed snapshot restores");

            assert_eq!(report.session_id, SessionId::new("restored-session"));
            assert_eq!(report.restored_event_count, 6);
            assert!(report.repaired_events.is_empty());
            assert!(report.interrupted_runs.is_empty());

            let session = sessions
                .session(&SessionId::new("restored-session"))
                .expect("session is restored");
            assert_eq!(session.status, SessionStatus::Active);
            assert_eq!(session.next_sequence, 6);
            assert_eq!(
                session.runs.get(&RunId::new("run-1")),
                Some(&RunStatus::Finished)
            );
        }

        #[test]
        fn rejects_an_empty_snapshot() {
            let mut sessions = manager();
            let error = sessions
                .restore_session(
                    SessionSnapshot { events: vec![] },
                    CommandId::new("restore-1"),
                    None,
                )
                .expect_err("an empty snapshot cannot be restored");
            assert_eq!(error.code, ErrorCode::InvalidSnapshot);
        }

        #[test]
        fn rejects_a_non_contiguous_sequence() {
            let mut events = healthy_history();
            events.remove(2); // drop sequence 3, leaving a 2 -> 4 gap
            let mut sessions = manager();
            let error = sessions
                .restore_session(
                    SessionSnapshot { events },
                    CommandId::new("restore-1"),
                    None,
                )
                .expect_err("a gap in the sequence cannot be restored");
            assert_eq!(error.code, ErrorCode::InvalidSnapshot);
        }

        #[test]
        fn rejects_a_forked_session() {
            let events = vec![snap_event(
                1,
                None,
                Event::SessionForked {
                    source_session_id: SessionId::new("parent"),
                },
            )];
            let mut sessions = manager();
            let error = sessions
                .restore_session(
                    SessionSnapshot { events },
                    CommandId::new("restore-1"),
                    None,
                )
                .expect_err("phase 2 does not restore a forked session");
            assert_eq!(error.code, ErrorCode::InvalidSnapshot);
        }

        #[test]
        fn rejects_restoring_over_an_already_open_session_id() {
            let mut sessions = manager();
            sessions
                .restore_session(
                    SessionSnapshot {
                        events: healthy_history(),
                    },
                    CommandId::new("restore-1"),
                    None,
                )
                .expect("first restore succeeds");
            let error = sessions
                .restore_session(
                    SessionSnapshot {
                        events: healthy_history(),
                    },
                    CommandId::new("restore-2"),
                    None,
                )
                .expect_err("a second restore of the same session id must not clobber the first");
            assert_eq!(error.code, ErrorCode::InvalidSessionState);
        }

        #[test]
        fn derives_status_from_the_last_lifecycle_event() {
            let mut events = healthy_history();
            events.push(snap_event(7, None, Event::SessionSuspended));
            let mut sessions = manager();
            sessions
                .restore_session(
                    SessionSnapshot { events },
                    CommandId::new("restore-1"),
                    None,
                )
                .expect("restores");
            assert_eq!(
                sessions
                    .session(&SessionId::new("restored-session"))
                    .unwrap()
                    .status,
                SessionStatus::Suspended
            );
        }

        #[test]
        fn repairs_an_interrupted_run_before_marking_it_failed() {
            // The process ended mid-run: a tool call was requested but never
            // completed, and there is no run.completed/failed/cancelled at
            // all -- exactly what a crash mid-`shell` call would leave
            // behind in the file.
            let events = vec![
                snap_event(
                    1,
                    None,
                    Event::SessionCreated {
                        workspace_id: WorkspaceId::new("ws-1"),
                    },
                ),
                snap_event(2, Some("run-1"), Event::RunScheduled),
                snap_event(3, Some("run-1"), Event::RunStarted),
                snap_event(
                    4,
                    Some("run-1"),
                    Event::ToolCallRequested {
                        call_id: "call-1".to_owned(),
                        name: "shell".to_owned(),
                        arguments: serde_json::json!({"command": "sleep 100"}),
                        provider_state: None,
                    },
                ),
            ];
            let mut sessions = manager();
            let report = sessions
                .restore_session(
                    SessionSnapshot { events },
                    CommandId::new("restore-1"),
                    None,
                )
                .expect("an interrupted run still restores, repaired");

            assert_eq!(report.interrupted_runs, vec![RunId::new("run-1")]);
            assert_eq!(
                report.repaired_events.len(),
                2,
                "one synthetic completed, one run.failed"
            );
            assert!(matches!(
                report.repaired_events[0].event,
                Event::ToolCallCompleted { is_error: true, .. }
            ));
            assert!(matches!(
                report.repaired_events[1].event,
                Event::RunFailed { .. }
            ));

            let session = sessions
                .session(&SessionId::new("restored-session"))
                .unwrap();
            assert_eq!(
                session.runs.get(&RunId::new("run-1")),
                Some(&RunStatus::Failed)
            );
            // The repair Events must actually be part of the session's own
            // log, not just the report, with sequence numbers continuing
            // from where the snapshot left off (4), not restarting.
            assert_eq!(session.events.len(), 6);
            assert_eq!(session.events[4].sequence, 5);
            assert_eq!(session.events[5].sequence, 6);
            assert_eq!(session.next_sequence, 6);
        }

        #[test]
        fn a_completed_tool_call_in_an_interrupted_run_is_not_repaired_again() {
            // The run itself never got a terminal Event, but this specific
            // call did finish before the crash -- only the run needs a
            // run.failed, the call must not get a second, contradictory
            // "completed" synthesized on top of its real one.
            let events = vec![
                snap_event(
                    1,
                    None,
                    Event::SessionCreated {
                        workspace_id: WorkspaceId::new("ws-1"),
                    },
                ),
                snap_event(2, Some("run-1"), Event::RunScheduled),
                snap_event(3, Some("run-1"), Event::RunStarted),
                snap_event(
                    4,
                    Some("run-1"),
                    Event::ToolCallRequested {
                        call_id: "call-1".to_owned(),
                        name: "read_file".to_owned(),
                        arguments: serde_json::json!({"path": "a.txt"}),
                        provider_state: None,
                    },
                ),
                snap_event(
                    5,
                    Some("run-1"),
                    Event::ToolCallCompleted {
                        call_id: "call-1".to_owned(),
                        name: "read_file".to_owned(),
                        result: "contents".to_owned(),
                        is_error: false,
                    },
                ),
            ];
            let mut sessions = manager();
            let report = sessions
                .restore_session(
                    SessionSnapshot { events },
                    CommandId::new("restore-1"),
                    None,
                )
                .expect("restores");
            assert_eq!(
                report.repaired_events.len(),
                1,
                "only run.failed, no synthetic completed for the call that already has a real one"
            );
            assert!(matches!(
                report.repaired_events[0].event,
                Event::RunFailed { .. }
            ));
        }

        #[test]
        fn restoring_advances_a_sequential_allocator_past_the_restored_run() {
            let mut sessions = SessionManager::with_ids(
                CoreRuntime::new(EchoModel::default(), NoopRunner),
                Box::new(SequentialIds::default()),
            );
            sessions
                .restore_session(
                    SessionSnapshot {
                        events: healthy_history(),
                    },
                    CommandId::new("restore-1"),
                    None,
                )
                .expect("restores");
            // The snapshot's own run was "run-1"; a fresh SequentialIds
            // would hand that id out again next, colliding with the
            // restored run's own history.
            assert_eq!(sessions.allocate_run_id(), RunId::new("run-2"));
        }

        #[test]
        fn an_attached_observer_sees_the_repair_events() {
            #[derive(Default)]
            struct Recording(Mutex<Vec<Event>>);
            impl SessionEventObserver for Recording {
                fn observe(&self, envelope: &EventEnvelope, _visibility: EventVisibility) {
                    self.0.lock().unwrap().push(envelope.event.clone());
                }
            }
            let events = vec![
                snap_event(
                    1,
                    None,
                    Event::SessionCreated {
                        workspace_id: WorkspaceId::new("ws-1"),
                    },
                ),
                snap_event(2, Some("run-1"), Event::RunScheduled),
            ];
            let recorder = Recording::default();
            let mut sessions = manager();
            sessions
                .restore_session(
                    SessionSnapshot { events },
                    CommandId::new("restore-1"),
                    Some(&recorder),
                )
                .expect("restores");
            let recorded = recorder.0.lock().unwrap();
            assert!(
                recorded
                    .iter()
                    .any(|event| matches!(event, Event::RunFailed { .. })),
                "the observer must see the repair run.failed Event, not just the report"
            );
        }
    }
}
