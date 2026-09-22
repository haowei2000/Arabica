//! `structure acp`: Agent Client Protocol v1 over stdio.
//!
//! See `docs/runtime_core_architecture.md` Appendix B for the decision
//! record and `crates/structure-cli/src/host.rs` for the composition this
//! builds on. Every `session/new` gets its own `SessionManager` (plan
//! decision #8: one lock per session, not a single lock serializing every
//! Zed window), and every `session/prompt` runs the turn on a spawned task
//! (`docs/runtime_core_architecture.md` §9) so the JSON-RPC dispatch loop
//! stays free to deliver `session/cancel` and permission responses while a
//! turn is in flight -- see `concepts::ordering` in the `agent-client-protocol`
//! crate for why a handler must never `.await` its own run to completion.

mod content;
mod mapping;
mod permission;
mod stop_reason;
mod time;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use agent_client_protocol::schema::ProtocolVersion;
use agent_client_protocol::schema::v1::{
    AgentCapabilities, CancelNotification, CloseSessionRequest, CloseSessionResponse,
    Implementation, InitializeRequest, InitializeResponse, ListSessionsRequest,
    ListSessionsResponse, LoadSessionRequest, LoadSessionResponse, NewSessionRequest,
    NewSessionResponse, PromptRequest, PromptResponse, ResumeSessionRequest, ResumeSessionResponse,
    SessionCapabilities, SessionCloseCapabilities, SessionId as AcpSessionId, SessionInfo,
    SessionListCapabilities, SessionNotification, SessionResumeCapabilities,
};
use agent_client_protocol::{
    Agent, Client, ConnectionTo, Error as AcpError, Responder, Result as AcpResult, Stdio,
};
use structure_adapters::{FileSessionStore, NewSession};
use structure_protocol::{
    Command, CommandEnvelope, CommandId, RunId as StructureRunId, SessionId as StructureSessionId,
};
use structure_runtime::{RunCancellation, RunControl, ToolPermissionGate};
use structure_session::{
    DispatchControl, EventVisibility, FanOutObserver, IdAllocator, SessionError,
    SessionEventObserver, SessionManager,
};

use crate::host::{HostModel, HostRuntime, LocalRunnerPolicy, build_host_runtime};
use structure_provider::{ApiModelProvider, ApiProviderConfig};

/// Structure's own session/run ids only need to be unique within one
/// `SessionManager`, but this host runs one `SessionManager` per ACP
/// session, and every session's ids end up in the same log stream (stderr,
/// and eventually a shared session store per Phase 2). Sequential ids would
/// make every session's first run "run-1"; UUIDv7 keeps them distinguishable
/// and, being time-ordered, still sort the way a human skimming a log
/// expects.
#[derive(Debug, Default)]
struct UuidIds;

impl IdAllocator for UuidIds {
    fn session_id(&mut self) -> StructureSessionId {
        StructureSessionId::new(uuid::Uuid::now_v7().to_string())
    }

    fn run_id(&mut self) -> StructureRunId {
        StructureRunId::new(uuid::Uuid::now_v7().to_string())
    }
}

struct SessionEntry {
    manager: Arc<tokio::sync::Mutex<SessionManager<HostRuntime>>>,
    structure_session_id: StructureSessionId,
    cwd: PathBuf,
    /// The in-flight run's cancellation handle, if any. `session/cancel`
    /// reads this; `session/prompt` sets it for the run it starts and
    /// clears it when that run ends.
    current_run: Mutex<Option<RunCancellation>>,
    /// Persists every Event for this session across its whole ACP
    /// connection lifetime (every `session/prompt`, not just one), unlike
    /// `print.rs`'s `run_task`, which builds a fresh store for one run and
    /// drops it. `Arc` because `handle_prompt` shares it into a
    /// per-call `FanOutObserver` alongside the live `AcpObserver`.
    store: Arc<FileSessionStore>,
}

/// Builds one fresh [`HostModel`] per `session/new`: every ACP session gets
/// its own model instance (`HostModel` holds per-connection state and is not
/// `Clone`), all built from the same resolved configuration. Boxed so tests
/// can substitute a `Scripted` model without touching a real provider.
type ModelFactory = dyn Fn() -> Result<HostModel, AcpError> + Send + Sync;

#[derive(Clone)]
struct AcpState {
    model_factory: Arc<ModelFactory>,
    tool_policy: LocalRunnerPolicy,
    structure_home: PathBuf,
    sessions: Arc<Mutex<HashMap<AcpSessionId, Arc<SessionEntry>>>>,
}

impl AcpState {
    fn new(
        model_factory: Arc<ModelFactory>,
        tool_policy: LocalRunnerPolicy,
        structure_home: PathBuf,
    ) -> Self {
        Self {
            model_factory,
            tool_policy,
            structure_home,
            sessions: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    fn entry(&self, session_id: &AcpSessionId) -> Option<Arc<SessionEntry>> {
        self.sessions
            .lock()
            .expect("session map lock poisoned")
            .get(session_id)
            .cloned()
    }

    async fn new_session(&self, request: NewSessionRequest) -> AcpResult<NewSessionResponse> {
        if !request.mcp_servers.is_empty() {
            eprintln!(
                "structure acp: ignoring {} MCP server(s) from session/new; this agent's tools run in-process, not over MCP",
                request.mcp_servers.len()
            );
        }
        if !request.cwd.is_absolute() {
            return Err(AcpError::invalid_params()
                .data(format!("cwd must be absolute: {}", request.cwd.display())));
        }
        let workspace_id = crate::host::workspace_id_for(&request.cwd);
        let model = (self.model_factory)()?;
        let runtime = build_host_runtime(model, &request.cwd, self.tool_policy.clone());
        let mut manager = SessionManager::with_ids(runtime, Box::new(UuidIds));
        let envelope = CommandEnvelope::new(
            CommandId::new(uuid::Uuid::now_v7().to_string()),
            None,
            Command::SessionCreate {
                workspace_id: workspace_id.clone(),
            },
        );
        let events = manager
            .dispatch(envelope, DispatchControl::default())
            .await
            .map_err(session_error)?;
        // dispatch's own return value always carries the produced Events
        // regardless of whether an observer was attached, which is the only
        // way to get at session.created here at all: the store below needs
        // this session's id to name its file, but that id is allocated
        // *inside* this very dispatch call, so the store cannot exist yet to
        // observe it live. `store.observe` a few lines down closes that gap
        // by hand (the same pattern `print.rs`'s `run_task` uses).
        let session_created = events
            .first()
            .ok_or_else(|| AcpError::internal_error().data("session.create produced no Events"))?;
        let structure_session_id = session_created.session_id.clone();
        let acp_session_id = AcpSessionId::new(structure_session_id.to_string());

        let store = FileSessionStore::create(
            &self.structure_home,
            NewSession {
                session_id: &structure_session_id,
                workspace_id: &workspace_id,
                cwd: &request.cwd,
                profile: None,
                instructions_sha256: None,
            },
        )
        .map_err(|error| AcpError::internal_error().data(error.to_string()))?;
        store.observe(session_created, EventVisibility::Client);

        self.sessions
            .lock()
            .expect("session map lock poisoned")
            .insert(
                acp_session_id.clone(),
                Arc::new(SessionEntry {
                    manager: Arc::new(tokio::sync::Mutex::new(manager)),
                    structure_session_id,
                    cwd: request.cwd,
                    current_run: Mutex::new(None),
                    store: Arc::new(store),
                }),
            );
        Ok(NewSessionResponse::new(acp_session_id))
    }

    /// `session/load`: only reachable once `initialize` has advertised
    /// `agentCapabilities.loadSession` (set unconditionally in `serve` below,
    /// since this handler exists). Replay-then-respond is the client's
    /// contract (`agent_client_protocol::session::RestoreSessionBuilder`'s
    /// own doc comment: "replay notifications sent before the response are
    /// available"), so nothing is sent to the client until the session is
    /// known-restorable -- a `session/load` that fails must not have already
    /// shown the client a history for a session it cannot actually resume.
    async fn load_session(
        &self,
        request: LoadSessionRequest,
        connection: &ConnectionTo<Client>,
    ) -> AcpResult<LoadSessionResponse> {
        if !request.cwd.is_absolute() {
            return Err(AcpError::invalid_params()
                .data(format!("cwd must be absolute: {}", request.cwd.display())));
        }
        let workspace_id = crate::host::workspace_id_for(&request.cwd);
        let structure_session_id = StructureSessionId::new(request.session_id.to_string());

        let stored = FileSessionStore::read_session(
            &self.structure_home,
            &workspace_id,
            &structure_session_id,
        )
        .map_err(|error| {
            AcpError::invalid_params().data(format!(
                "cannot load session {}: {error}",
                request.session_id
            ))
        })?;
        let original_events = stored.events.clone();

        let path = FileSessionStore::session_path(
            &self.structure_home,
            &workspace_id,
            &structure_session_id,
        );
        let store = FileSessionStore::open_existing(&path)
            .map_err(|error| AcpError::internal_error().data(error.to_string()))?;

        let model = (self.model_factory)()?;
        let runtime = build_host_runtime(model, &request.cwd, self.tool_policy.clone());
        let mut manager = SessionManager::with_ids(runtime, Box::new(UuidIds));
        let restore_report = manager
            .restore_session(
                stored.into_snapshot(),
                CommandId::new(uuid::Uuid::now_v7().to_string()),
                Some(&store as &dyn SessionEventObserver),
            )
            .map_err(session_error)?;

        let acp_session_id = AcpSessionId::new(structure_session_id.to_string());
        // Original history first, then any repair Events restore_session
        // synthesized for a run the previous connection never got to finish
        // (dangling tool calls, then run.failed) -- the repairs are new
        // Events appended after the stored history, not part of it, so they
        // belong after it in replay order too.
        for envelope in original_events
            .iter()
            .chain(restore_report.repaired_events.iter())
        {
            for update in mapping::updates_for(&envelope.event, &envelope.run_id, &request.cwd) {
                if let Err(error) = connection
                    .send_notification(SessionNotification::new(acp_session_id.clone(), update))
                {
                    eprintln!(
                        "structure acp: dropped a session/update during session/load replay: {error}"
                    );
                }
            }
        }

        self.sessions
            .lock()
            .expect("session map lock poisoned")
            .insert(
                acp_session_id,
                Arc::new(SessionEntry {
                    manager: Arc::new(tokio::sync::Mutex::new(manager)),
                    structure_session_id,
                    cwd: request.cwd,
                    current_run: Mutex::new(None),
                    store: Arc::new(store),
                }),
            );

        Ok(LoadSessionResponse::new())
    }

    /// `session/resume`: only reachable once `initialize` has advertised
    /// `agentCapabilities.sessionCapabilities.resume`. The restore half of
    /// `load_session`, without the replay half -- "resume without returning
    /// previous messages" is the method's own documented contract, not a
    /// shortcut taken here. Deliberately a near-twin of `load_session`
    /// rather than a shared helper: the two methods differ in exactly one
    /// self-contained slice (the replay loop and its `connection` parameter)
    /// bracketed by identical restore logic on both sides, and factoring
    /// that out would cost more in indirection than it would save in lines.
    async fn resume_session(
        &self,
        request: ResumeSessionRequest,
    ) -> AcpResult<ResumeSessionResponse> {
        if !request.cwd.is_absolute() {
            return Err(AcpError::invalid_params()
                .data(format!("cwd must be absolute: {}", request.cwd.display())));
        }
        let workspace_id = crate::host::workspace_id_for(&request.cwd);
        let structure_session_id = StructureSessionId::new(request.session_id.to_string());

        let stored = FileSessionStore::read_session(
            &self.structure_home,
            &workspace_id,
            &structure_session_id,
        )
        .map_err(|error| {
            AcpError::invalid_params().data(format!(
                "cannot resume session {}: {error}",
                request.session_id
            ))
        })?;

        let path = FileSessionStore::session_path(
            &self.structure_home,
            &workspace_id,
            &structure_session_id,
        );
        let store = FileSessionStore::open_existing(&path)
            .map_err(|error| AcpError::internal_error().data(error.to_string()))?;

        let model = (self.model_factory)()?;
        let runtime = build_host_runtime(model, &request.cwd, self.tool_policy.clone());
        let mut manager = SessionManager::with_ids(runtime, Box::new(UuidIds));
        manager
            .restore_session(
                stored.into_snapshot(),
                CommandId::new(uuid::Uuid::now_v7().to_string()),
                Some(&store as &dyn SessionEventObserver),
            )
            .map_err(session_error)?;

        let acp_session_id = AcpSessionId::new(structure_session_id.to_string());
        self.sessions
            .lock()
            .expect("session map lock poisoned")
            .insert(
                acp_session_id,
                Arc::new(SessionEntry {
                    manager: Arc::new(tokio::sync::Mutex::new(manager)),
                    structure_session_id,
                    cwd: request.cwd,
                    current_run: Mutex::new(None),
                    store: Arc::new(store),
                }),
            );

        Ok(ResumeSessionResponse::new())
    }

    /// `session/list`: only reachable once `initialize` has advertised
    /// `agentCapabilities.sessionCapabilities.list` (set unconditionally in
    /// `serve` below, since this handler exists). Synchronous: unlike
    /// `new_session`/`load_session`, nothing here touches a runtime or a
    /// model, only `$STRUCTURE_HOME` on disk.
    fn list_sessions(&self, request: ListSessionsRequest) -> AcpResult<ListSessionsResponse> {
        if let Some(cwd) = &request.cwd
            && !cwd.is_absolute()
        {
            return Err(
                AcpError::invalid_params().data(format!("cwd must be absolute: {}", cwd.display()))
            );
        }
        // No cwd filter means every workspace, the same "cwd absent" ->
        // "no workspace scope" mapping print mode's `structure sessions
        // list --all` uses (`FileSessionStore::list_sessions`'s own
        // `workspace_id: Option<&WorkspaceId>` parameter).
        let workspace_id = request
            .cwd
            .as_ref()
            .map(|cwd| crate::host::workspace_id_for(cwd));
        let listings = FileSessionStore::list_sessions(&self.structure_home, workspace_id.as_ref())
            .map_err(|error| AcpError::internal_error().data(error.to_string()))?;
        let sessions = listings
            .into_iter()
            .map(|listing| {
                let mut info = SessionInfo::new(
                    AcpSessionId::new(listing.header.id.to_string()),
                    listing.header.cwd,
                );
                if let Some(modified_at_ms) = listing.modified_at_ms {
                    info = info.updated_at(time::to_iso8601(modified_at_ms));
                }
                info
            })
            .collect();
        Ok(ListSessionsResponse::new(sessions))
    }

    fn cancel(&self, session_id: &AcpSessionId) {
        let Some(entry) = self.entry(session_id) else {
            return;
        };
        if let Some(cancellation) = entry
            .current_run
            .lock()
            .expect("current_run lock poisoned")
            .as_ref()
        {
            cancellation.cancel();
        }
    }

    /// `session/close`: only reachable once `initialize` has advertised
    /// `agentCapabilities.sessionCapabilities.close`. Maps to
    /// `Command::SessionSuspend`, not `Command::SessionClose` -- Structure's
    /// own `session.close` is a terminal state no further Command can move a
    /// session out of, but ACP's `session/close` only means "this
    /// connection is done with the session for now," and the same session
    /// can still come back later through `session/load`/`session/resume`.
    /// Any in-flight run is cancelled first (the same non-blocking signal
    /// `session/cancel` sends) before waiting on the session's own lock, so
    /// this does not hang waiting for a run that would otherwise keep going
    /// on its own; removed from `self.sessions` only once the suspend
    /// itself has actually succeeded, so a rejected suspend leaves the
    /// session exactly as promptable as it was before this call.
    async fn close_session(&self, request: CloseSessionRequest) -> AcpResult<CloseSessionResponse> {
        let Some(entry) = self.entry(&request.session_id) else {
            return Err(
                AcpError::invalid_params().data(format!("unknown session {}", request.session_id))
            );
        };
        self.cancel(&request.session_id);

        let envelope = CommandEnvelope::new(
            CommandId::new(uuid::Uuid::now_v7().to_string()),
            Some(entry.structure_session_id.clone()),
            Command::SessionSuspend,
        );
        let control = DispatchControl {
            run: RunControl::default(),
            observer: Some(Arc::clone(&entry.store) as Arc<dyn SessionEventObserver>),
        };
        let mut guard = entry.manager.lock().await;
        guard
            .dispatch(envelope, control)
            .await
            .map_err(session_error)?;
        drop(guard);

        self.sessions
            .lock()
            .expect("session map lock poisoned")
            .remove(&request.session_id);

        Ok(CloseSessionResponse::new())
    }
}

fn provider_error(error: structure_provider::ProviderError) -> AcpError {
    AcpError::internal_error().data(error.to_string())
}

fn session_error(error: SessionError) -> AcpError {
    match error.code {
        structure_protocol::ErrorCode::SessionNotFound
        | structure_protocol::ErrorCode::InvalidCommand
        | structure_protocol::ErrorCode::InvalidSessionState
        | structure_protocol::ErrorCode::ProtocolVersionMismatch => {
            AcpError::invalid_params().data(error.message)
        }
        _ => AcpError::internal_error().data(error.message),
    }
}

/// Runs `structure acp` to completion (until stdin closes). Every prompt
/// turn asks the ACP client for permission through `permission::bridge` and
/// streams progress through `mapping::AcpObserver`; neither ever writes to
/// stdout, which carries only this connection's own JSON-RPC frames.
pub async fn run(
    provider_config: ApiProviderConfig,
    tool_policy: LocalRunnerPolicy,
) -> AcpResult<()> {
    let structure_home = structure_adapters::default_structure_home()
        .map_err(|error| AcpError::internal_error().data(error.to_string()))?;
    let model_factory: Arc<ModelFactory> = Arc::new(move || {
        ApiModelProvider::new(provider_config.clone())
            .map(HostModel::Api)
            .map_err(provider_error)
    });
    serve(
        AcpState::new(model_factory, tool_policy, structure_home),
        Stdio::new(),
    )
    .await
}

/// The handler chain, generic over the transport so tests can drive it
/// through an in-memory [`agent_client_protocol::Channel`] instead of real
/// stdio.
async fn serve(
    state: AcpState,
    transport: impl agent_client_protocol::ConnectTo<Agent>,
) -> AcpResult<()> {
    Agent
        .builder()
        .name("structure")
        .on_receive_request(
            async move |request: InitializeRequest,
                        responder: Responder<InitializeResponse>,
                        _connection: ConnectionTo<Client>| {
                let _ = request.protocol_version;
                responder.respond(
                    InitializeResponse::new(ProtocolVersion::V1)
                        .agent_capabilities(
                            AgentCapabilities::new()
                                .load_session(true)
                                .session_capabilities(
                                    SessionCapabilities::new()
                                        .list(SessionListCapabilities::new())
                                        .resume(SessionResumeCapabilities::new())
                                        .close(SessionCloseCapabilities::new()),
                                ),
                        )
                        .agent_info(Implementation::new("structure", env!("CARGO_PKG_VERSION"))),
                )
            },
            agent_client_protocol::on_receive_request!(),
        )
        .on_receive_request(
            {
                let state = state.clone();
                async move |request: NewSessionRequest,
                            responder: Responder<NewSessionResponse>,
                            _connection: ConnectionTo<Client>| {
                    match state.new_session(request).await {
                        Ok(response) => responder.respond(response),
                        Err(error) => responder.respond_with_error(error),
                    }
                }
            },
            agent_client_protocol::on_receive_request!(),
        )
        .on_receive_request(
            {
                let state = state.clone();
                async move |request: PromptRequest,
                            responder: Responder<PromptResponse>,
                            connection: ConnectionTo<Client>| {
                    handle_prompt(state.clone(), request, responder, connection)
                }
            },
            agent_client_protocol::on_receive_request!(),
        )
        .on_receive_request(
            {
                let state = state.clone();
                async move |request: LoadSessionRequest,
                            responder: Responder<LoadSessionResponse>,
                            connection: ConnectionTo<Client>| {
                    match state.load_session(request, &connection).await {
                        Ok(response) => responder.respond(response),
                        Err(error) => responder.respond_with_error(error),
                    }
                }
            },
            agent_client_protocol::on_receive_request!(),
        )
        .on_receive_request(
            {
                let state = state.clone();
                async move |request: ListSessionsRequest,
                            responder: Responder<ListSessionsResponse>,
                            _connection: ConnectionTo<Client>| {
                    match state.list_sessions(request) {
                        Ok(response) => responder.respond(response),
                        Err(error) => responder.respond_with_error(error),
                    }
                }
            },
            agent_client_protocol::on_receive_request!(),
        )
        .on_receive_request(
            {
                let state = state.clone();
                async move |request: ResumeSessionRequest,
                            responder: Responder<ResumeSessionResponse>,
                            _connection: ConnectionTo<Client>| {
                    match state.resume_session(request).await {
                        Ok(response) => responder.respond(response),
                        Err(error) => responder.respond_with_error(error),
                    }
                }
            },
            agent_client_protocol::on_receive_request!(),
        )
        .on_receive_request(
            {
                let state = state.clone();
                async move |request: CloseSessionRequest,
                            responder: Responder<CloseSessionResponse>,
                            _connection: ConnectionTo<Client>| {
                    match state.close_session(request).await {
                        Ok(response) => responder.respond(response),
                        Err(error) => responder.respond_with_error(error),
                    }
                }
            },
            agent_client_protocol::on_receive_request!(),
        )
        .on_receive_notification(
            {
                let state = state.clone();
                async move |notification: CancelNotification, _connection: ConnectionTo<Client>| {
                    state.cancel(&notification.session_id);
                    Ok(())
                }
            },
            agent_client_protocol::on_receive_notification!(),
        )
        .connect_to(transport)
        .await
}

/// The `session/prompt` handler proper, split out of the closure only for
/// readability: look up the session and claim its manager synchronously (so
/// a concurrent second prompt on the same session is rejected immediately,
/// not queued), then hand the actual turn to a spawned task and return.
fn handle_prompt(
    state: AcpState,
    request: PromptRequest,
    responder: Responder<PromptResponse>,
    connection: ConnectionTo<Client>,
) -> AcpResult<()> {
    let Some(entry) = state.entry(&request.session_id) else {
        return responder.respond_with_error(
            AcpError::invalid_params().data(format!("unknown session {}", request.session_id)),
        );
    };
    let Ok(guard) = Arc::clone(&entry.manager).try_lock_owned() else {
        return responder.respond_with_error(
            AcpError::invalid_request().data("a prompt is already in progress for this session"),
        );
    };

    let content = content::prompt_text(&request.prompt);
    let envelope = CommandEnvelope::new(
        CommandId::new(uuid::Uuid::now_v7().to_string()),
        Some(entry.structure_session_id.clone()),
        Command::MessageSend { content },
    );
    let cancellation = RunCancellation::new();
    *entry.current_run.lock().expect("current_run lock poisoned") = Some(cancellation.clone());

    let (permission_tx, permission_rx) = tokio::sync::mpsc::unbounded_channel();
    let observer: Arc<dyn SessionEventObserver> = Arc::new(FanOutObserver::new(vec![
        Arc::clone(&entry.store) as Arc<dyn SessionEventObserver>,
        Arc::new(mapping::AcpObserver::new(
            connection.clone(),
            request.session_id.clone(),
            entry.cwd.clone(),
        )) as Arc<dyn SessionEventObserver>,
    ]));
    let control = DispatchControl {
        run: RunControl {
            cancellation: Some(cancellation),
            permissions: Some(ToolPermissionGate {
                policy: permission::default_policy(),
                approver: Some(permission_tx),
            }),
        },
        observer: Some(observer),
    };

    let session_id = request.session_id.clone();
    let permission_connection = connection.clone();
    let permission_cwd = entry.cwd.clone();
    connection.spawn(async move {
        tokio::spawn(permission::bridge(
            permission_rx,
            permission_connection,
            session_id,
            permission_cwd,
        ));
        let mut guard = guard;
        let result = guard.dispatch(envelope, control).await;
        drop(guard);
        *entry.current_run.lock().expect("current_run lock poisoned") = None;

        let response = match result {
            Ok(events) => match stop_reason::classify(&events) {
                stop_reason::PromptOutcome::Stopped(reason) => {
                    responder.respond(PromptResponse::new(reason))
                }
                stop_reason::PromptOutcome::Failed(message) => {
                    responder.respond_with_error(AcpError::internal_error().data(message))
                }
            },
            Err(error) => responder.respond_with_error(session_error(error)),
        };
        if let Err(error) = response {
            eprintln!("structure acp: failed to send a session/prompt response: {error}");
        }
        Ok(())
    })
}

#[cfg(test)]
mod round_trip {
    //! Drives `serve` over an in-memory `Channel::duplex()` with a real
    //! `Client` builder standing in for the editor, so the concurrency this
    //! module depends on (`cx.spawn`, the live observer, the permission
    //! bridge) is exercised for real rather than assumed from reading the
    //! code. A fuller scenario matrix (cancellation mid-run, step limits,
    //! provider errors, a real `structure acp` binary against a mock HTTP
    //! server) is T9's job; this is the minimum that must work for T7 to be
    //! trustworthy at all.

    use std::path::Path;
    use std::sync::Mutex as StdMutex;
    use std::time::Duration;

    use agent_client_protocol::schema::v1::{
        CloseSessionRequest as AcpCloseSessionRequest, ContentBlock as AcpContentBlock,
        InitializeRequest as AcpInitializeRequest, ListSessionsRequest as AcpListSessionsRequest,
        LoadSessionRequest as AcpLoadSessionRequest, NewSessionRequest as AcpNewSessionRequest,
        PermissionOptionKind, PromptRequest as AcpPromptRequest, RequestPermissionOutcome,
        RequestPermissionRequest, RequestPermissionResponse,
        ResumeSessionRequest as AcpResumeSessionRequest, SelectedPermissionOutcome,
        SessionNotification, SessionUpdate, StopReason, ToolCallStatus,
    };
    use agent_client_protocol::{Channel, Client as ClientRole, Responder};
    use structure_model::{
        FinishReason, MessageItem, RuntimeItem, RuntimeResponse, RuntimeRole, RuntimeUsage,
        ToolCallItem,
    };
    use structure_provider::ModelRunResult;

    use super::*;
    use crate::host::ScriptedModel;

    fn text_result(text: &str) -> ModelRunResult {
        ModelRunResult {
            final_output: Some(text.to_owned()),
            prepared_request: None,
            response: Some(RuntimeResponse {
                items: vec![RuntimeItem::Message(MessageItem::text(
                    RuntimeRole::Assistant,
                    text,
                ))],
                finish_reason: Some(FinishReason::Stop),
                usage: RuntimeUsage::default(),
                provider_state: None,
            }),
        }
    }

    fn tool_call_result(name: &str, arguments: serde_json::Value) -> ModelRunResult {
        ModelRunResult {
            final_output: None,
            prepared_request: None,
            response: Some(RuntimeResponse {
                items: vec![RuntimeItem::ToolCall(ToolCallItem {
                    id: None,
                    call_id: "call-1".to_owned(),
                    name: name.to_owned(),
                    arguments,
                    provider_state: None,
                })],
                finish_reason: Some(FinishReason::ToolCalls),
                usage: RuntimeUsage::default(),
                provider_state: None,
            }),
        }
    }

    fn temp_root(label: &str) -> PathBuf {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock is valid")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("structure-acp-round-trip-{label}-{unique}"));
        std::fs::create_dir_all(&root).expect("test root is created");
        root
    }

    fn scripted_state(
        results: Vec<ModelRunResult>,
        tool_policy: LocalRunnerPolicy,
        structure_home: &Path,
    ) -> AcpState {
        let results = StdMutex::new(Some(results));
        let model_factory: Arc<ModelFactory> = Arc::new(move || {
            Ok(HostModel::Scripted(ScriptedModel::new(
                results
                    .lock()
                    .expect("results lock poisoned")
                    .take()
                    .unwrap_or_default(),
            )))
        });
        AcpState::new(model_factory, tool_policy, structure_home.to_path_buf())
    }

    /// The exact policy `main.rs` builds for `structure acp` (`coding()`
    /// plus `shell`, opted in at the policy layer and gated by the
    /// permission bridge): tests that exercise the shell tool use this so a
    /// policy drift between the real binary and its own tests fails loudly.
    fn acp_tool_policy() -> LocalRunnerPolicy {
        LocalRunnerPolicy::coding().with_tool(structure_runner::LocalTool::Shell)
    }

    /// Runs the agent side on a background task and returns its `JoinHandle`
    /// alongside the client-role `ConnectionTo` half of an in-memory
    /// channel pair. Callers drive the client through `client`.
    fn spawn_agent(state: AcpState) -> (tokio::task::JoinHandle<AcpResult<()>>, Channel) {
        let (agent_channel, client_channel) = Channel::duplex();
        (tokio::spawn(serve(state, agent_channel)), client_channel)
    }

    #[tokio::test]
    async fn a_plain_text_prompt_ends_the_turn_and_streams_the_message() {
        let root = temp_root("plain-text");
        let structure_home = temp_root("plain-text-home");
        let state = scripted_state(
            vec![text_result("hello from structure")],
            LocalRunnerPolicy::coding(),
            &structure_home,
        );
        let (server, client_channel) = spawn_agent(state);
        let updates: Arc<StdMutex<Vec<SessionUpdate>>> = Arc::new(StdMutex::new(Vec::new()));

        let outcome = tokio::time::timeout(Duration::from_secs(10), {
            let updates = updates.clone();
            let root = root.clone();
            ClientRole
                .builder()
                .name("test-client")
                .on_receive_notification(
                    {
                        let updates = updates.clone();
                        async move |notification: SessionNotification, _connection| {
                            updates
                                .lock()
                                .expect("updates lock poisoned")
                                .push(notification.update);
                            Ok(())
                        }
                    },
                    agent_client_protocol::on_receive_notification!(),
                )
                .connect_with(client_channel, async move |cx| {
                    cx.send_request(AcpInitializeRequest::new(ProtocolVersion::V1))
                        .block_task()
                        .await?;
                    let new_session = cx
                        .send_request(AcpNewSessionRequest::new(root))
                        .block_task()
                        .await?;
                    let prompt = cx
                        .send_request(AcpPromptRequest::new(
                            new_session.session_id,
                            vec![AcpContentBlock::from("say hello")],
                        ))
                        .block_task()
                        .await?;
                    Ok(prompt.stop_reason)
                })
        })
        .await
        .expect("client round trip did not time out")
        .expect("client round trip succeeded");

        assert_eq!(outcome, StopReason::EndTurn);
        assert!(
            updates
                .lock()
                .expect("updates lock poisoned")
                .iter()
                .any(|update| matches!(update, SessionUpdate::AgentMessageChunk(_))),
            "expected a live agent_message_chunk update, got {:?}",
            updates.lock().expect("updates lock poisoned")
        );

        server
            .await
            .expect("server task did not panic")
            .expect("server run completed cleanly");
        std::fs::remove_dir_all(root).ok();
        std::fs::remove_dir_all(structure_home).ok();
    }

    #[tokio::test]
    async fn a_prompt_turn_persists_its_session_to_disk() {
        let root = temp_root("persist");
        let structure_home = temp_root("persist-home");
        let state = scripted_state(
            vec![text_result("hello from structure")],
            LocalRunnerPolicy::coding(),
            &structure_home,
        );
        let (server, client_channel) = spawn_agent(state);

        let session_id = tokio::time::timeout(Duration::from_secs(10), {
            let root = root.clone();
            ClientRole
                .builder()
                .name("test-client")
                .connect_with(client_channel, async move |cx| {
                    cx.send_request(AcpInitializeRequest::new(ProtocolVersion::V1))
                        .block_task()
                        .await?;
                    let new_session = cx
                        .send_request(AcpNewSessionRequest::new(root))
                        .block_task()
                        .await?;
                    cx.send_request(AcpPromptRequest::new(
                        new_session.session_id.clone(),
                        vec![AcpContentBlock::from("say hello")],
                    ))
                    .block_task()
                    .await?;
                    Ok(new_session.session_id)
                })
        })
        .await
        .expect("client round trip did not time out")
        .expect("client round trip succeeded");

        server
            .await
            .expect("server task did not panic")
            .expect("server run completed cleanly");

        // The ACP session id is exactly the stringified Structure session
        // id (`AcpState::new_session`), so this round-trips it back rather
        // than re-deriving anything the store itself would not have used.
        let workspace_id = crate::host::workspace_id_for(&root);
        let stored = FileSessionStore::read_session(
            &structure_home,
            &workspace_id,
            &structure_protocol::SessionId::new(session_id.to_string()),
        )
        .expect("the session persisted by the live observer is readable back");
        assert_eq!(stored.header.id.to_string(), session_id.to_string());
        assert!(
            matches!(
                stored.events.first().map(|envelope| &envelope.event),
                Some(structure_protocol::Event::SessionCreated { .. })
            ),
            "the first persisted event must be session.created, got {:?}",
            stored.events.first()
        );
        assert!(
            stored.events.len() > 1,
            "the prompt turn must have appended events beyond session.created, got {:?}",
            stored.events
        );

        std::fs::remove_dir_all(root).ok();
        std::fs::remove_dir_all(structure_home).ok();
    }

    fn chunk_text(update: &SessionUpdate) -> Option<&str> {
        let SessionUpdate::AgentMessageChunk(chunk) = update else {
            return None;
        };
        let AcpContentBlock::Text(text) = &chunk.content else {
            return None;
        };
        Some(&text.text)
    }

    #[tokio::test]
    async fn session_load_replays_history_then_accepts_a_new_prompt() {
        let root = temp_root("load");
        let structure_home = temp_root("load-home");

        // Connection 1: create a session and send one prompt, then let the
        // connection end -- simulating the editor (or the agent process)
        // disconnecting. Nothing here ever calls session/load.
        let state1 = scripted_state(
            vec![text_result("hello from turn one")],
            LocalRunnerPolicy::coding(),
            &structure_home,
        );
        let (server1, client_channel1) = spawn_agent(state1);
        let session_id = tokio::time::timeout(Duration::from_secs(10), {
            let root = root.clone();
            ClientRole.builder().name("test-client-1").connect_with(
                client_channel1,
                async move |cx| {
                    cx.send_request(AcpInitializeRequest::new(ProtocolVersion::V1))
                        .block_task()
                        .await?;
                    let new_session = cx
                        .send_request(AcpNewSessionRequest::new(root))
                        .block_task()
                        .await?;
                    cx.send_request(AcpPromptRequest::new(
                        new_session.session_id.clone(),
                        vec![AcpContentBlock::from("say hello")],
                    ))
                    .block_task()
                    .await?;
                    Ok(new_session.session_id)
                },
            )
        })
        .await
        .expect("first client round trip did not time out")
        .expect("first client round trip succeeded");

        server1
            .await
            .expect("server task did not panic")
            .expect("first connection's server run completed cleanly");

        // Connection 2: a brand new AcpState -- its `sessions` map starts
        // empty, with no entry for this session at all, the same as a fresh
        // `structure acp` process would have -- pointed at the SAME
        // structure_home. session/load must find the session on disk,
        // replay its history as session/update notifications, then accept a
        // new prompt on the session it just restored.
        let state2 = scripted_state(
            vec![text_result("hello again after loading")],
            LocalRunnerPolicy::coding(),
            &structure_home,
        );
        let (server2, client_channel2) = spawn_agent(state2);
        let updates: Arc<StdMutex<Vec<SessionUpdate>>> = Arc::new(StdMutex::new(Vec::new()));

        let second_stop_reason = tokio::time::timeout(Duration::from_secs(10), {
            let updates = updates.clone();
            let root = root.clone();
            let session_id = session_id.clone();
            ClientRole
                .builder()
                .name("test-client-2")
                .on_receive_notification(
                    {
                        let updates = updates.clone();
                        async move |notification: SessionNotification, _connection| {
                            updates
                                .lock()
                                .expect("updates lock poisoned")
                                .push(notification.update);
                            Ok(())
                        }
                    },
                    agent_client_protocol::on_receive_notification!(),
                )
                .connect_with(client_channel2, async move |cx| {
                    cx.send_request(AcpInitializeRequest::new(ProtocolVersion::V1))
                        .block_task()
                        .await?;
                    cx.send_request(AcpLoadSessionRequest::new(session_id.clone(), root))
                        .block_task()
                        .await?;
                    let prompt = cx
                        .send_request(AcpPromptRequest::new(
                            session_id,
                            vec![AcpContentBlock::from("what did you say before?")],
                        ))
                        .block_task()
                        .await?;
                    Ok(prompt.stop_reason)
                })
        })
        .await
        .expect("second client round trip did not time out")
        .expect("second client round trip succeeded");

        assert_eq!(second_stop_reason, StopReason::EndTurn);
        {
            let seen = updates.lock().expect("updates lock poisoned");
            assert!(
                seen.iter()
                    .any(|update| chunk_text(update) == Some("hello from turn one")),
                "session/load must replay the first turn's message as a session/update \
                 before responding, got {seen:?}"
            );
            assert!(
                seen.iter()
                    .any(|update| chunk_text(update) == Some("hello again after loading")),
                "the post-load prompt must also stream live, got {seen:?}"
            );
        }

        server2
            .await
            .expect("server task did not panic")
            .expect("second connection's server run completed cleanly");

        std::fs::remove_dir_all(root).ok();
        std::fs::remove_dir_all(structure_home).ok();
    }

    #[tokio::test]
    async fn session_resume_does_not_replay_history_but_does_accept_a_new_prompt() {
        let root = temp_root("resume");
        let structure_home = temp_root("resume-home");

        // Connection 1: same as session/load's own test -- create a
        // session, send one prompt, let the connection end.
        let state1 = scripted_state(
            vec![text_result("hello from turn one")],
            LocalRunnerPolicy::coding(),
            &structure_home,
        );
        let (server1, client_channel1) = spawn_agent(state1);
        let session_id = tokio::time::timeout(Duration::from_secs(10), {
            let root = root.clone();
            ClientRole.builder().name("test-client-1").connect_with(
                client_channel1,
                async move |cx| {
                    cx.send_request(AcpInitializeRequest::new(ProtocolVersion::V1))
                        .block_task()
                        .await?;
                    let new_session = cx
                        .send_request(AcpNewSessionRequest::new(root))
                        .block_task()
                        .await?;
                    cx.send_request(AcpPromptRequest::new(
                        new_session.session_id.clone(),
                        vec![AcpContentBlock::from("say hello")],
                    ))
                    .block_task()
                    .await?;
                    Ok(new_session.session_id)
                },
            )
        })
        .await
        .expect("first client round trip did not time out")
        .expect("first client round trip succeeded");

        server1
            .await
            .expect("server task did not panic")
            .expect("first connection's server run completed cleanly");

        // Connection 2: an independent AcpState (same reasoning as
        // session/load's test -- avoids the first connection's still-locked
        // FileSessionStore causing spurious lock contention) resumes the
        // session and sends a new prompt. Unlike session/load, no
        // session/update notifications should arrive before the prompt: the
        // whole point of session/resume is skipping that replay.
        let state2 = scripted_state(
            vec![text_result("hello again after resuming")],
            LocalRunnerPolicy::coding(),
            &structure_home,
        );
        let (server2, client_channel2) = spawn_agent(state2);
        let updates: Arc<StdMutex<Vec<SessionUpdate>>> = Arc::new(StdMutex::new(Vec::new()));

        let second_stop_reason = tokio::time::timeout(Duration::from_secs(10), {
            let updates = updates.clone();
            let root = root.clone();
            let session_id = session_id.clone();
            ClientRole
                .builder()
                .name("test-client-2")
                .on_receive_notification(
                    {
                        let updates = updates.clone();
                        async move |notification: SessionNotification, _connection| {
                            updates
                                .lock()
                                .expect("updates lock poisoned")
                                .push(notification.update);
                            Ok(())
                        }
                    },
                    agent_client_protocol::on_receive_notification!(),
                )
                .connect_with(client_channel2, async move |cx| {
                    cx.send_request(AcpInitializeRequest::new(ProtocolVersion::V1))
                        .block_task()
                        .await?;
                    cx.send_request(AcpResumeSessionRequest::new(session_id.clone(), root))
                        .block_task()
                        .await?;
                    let prompt = cx
                        .send_request(AcpPromptRequest::new(
                            session_id,
                            vec![AcpContentBlock::from("what did you say before?")],
                        ))
                        .block_task()
                        .await?;
                    Ok(prompt.stop_reason)
                })
        })
        .await
        .expect("second client round trip did not time out")
        .expect("second client round trip succeeded");

        assert_eq!(second_stop_reason, StopReason::EndTurn);
        {
            let seen = updates.lock().expect("updates lock poisoned");
            assert!(
                !seen
                    .iter()
                    .any(|update| chunk_text(update) == Some("hello from turn one")),
                "session/resume must NOT replay the first turn's message, got {seen:?}"
            );
            assert!(
                seen.iter()
                    .any(|update| chunk_text(update) == Some("hello again after resuming")),
                "the post-resume prompt must still stream live, got {seen:?}"
            );
        }

        server2
            .await
            .expect("server task did not panic")
            .expect("second connection's server run completed cleanly");

        std::fs::remove_dir_all(root).ok();
        std::fs::remove_dir_all(structure_home).ok();
    }

    #[tokio::test]
    async fn session_close_suspends_and_forgets_the_session_in_this_connection() {
        let root = temp_root("close");
        let structure_home = temp_root("close-home");
        let state = scripted_state(
            vec![text_result("hello before closing")],
            LocalRunnerPolicy::coding(),
            &structure_home,
        );
        let (server, client_channel) = spawn_agent(state);

        let (session_id, prompt_after_close_was_rejected, load_after_close_succeeded) =
            tokio::time::timeout(Duration::from_secs(10), {
                let root = root.clone();
                ClientRole.builder().name("test-client").connect_with(
                    client_channel,
                    async move |cx| {
                        cx.send_request(AcpInitializeRequest::new(ProtocolVersion::V1))
                            .block_task()
                            .await?;
                        let new_session = cx
                            .send_request(AcpNewSessionRequest::new(root.clone()))
                            .block_task()
                            .await?;
                        cx.send_request(AcpPromptRequest::new(
                            new_session.session_id.clone(),
                            vec![AcpContentBlock::from("say hello")],
                        ))
                        .block_task()
                        .await?;
                        cx.send_request(AcpCloseSessionRequest::new(
                            new_session.session_id.clone(),
                        ))
                        .block_task()
                        .await?;
                        // The session's own state (now Suspended) rejects a
                        // further message on its own -- state validation is
                        // a second, independent layer that would catch this
                        // even if close_session's own map bookkeeping had a
                        // bug, so this alone cannot prove that bookkeeping
                        // is correct.
                        let prompt_rejected = cx
                            .send_request(AcpPromptRequest::new(
                                new_session.session_id.clone(),
                                vec![AcpContentBlock::from("are you still there?")],
                            ))
                            .block_task()
                            .await
                            .is_err();
                        // This probe is the one that actually depends on
                        // close_session removing its own SessionEntry: a
                        // stale entry would keep its Arc<FileSessionStore>
                        // (and the exclusive flock that comes with it) alive
                        // forever, so session/load's own
                        // FileSessionStore::open_existing for the SAME
                        // session id would fail on lock contention with that
                        // never-released handle. Success here means no
                        // stale entry survived close.
                        let load_succeeded = cx
                            .send_request(AcpLoadSessionRequest::new(
                                new_session.session_id.clone(),
                                root,
                            ))
                            .block_task()
                            .await
                            .is_ok();
                        Ok((new_session.session_id, prompt_rejected, load_succeeded))
                    },
                )
            })
            .await
            .expect("client round trip did not time out")
            .expect("client round trip succeeded");

        assert!(
            prompt_after_close_was_rejected,
            "a session/prompt for a session already closed in this connection must be rejected"
        );
        assert!(
            load_after_close_succeeded,
            "session/load for a session already closed in this connection must succeed; a \
             failure here means close_session left a stale entry (and its locked \
             FileSessionStore) behind in the session map"
        );

        server
            .await
            .expect("server task did not panic")
            .expect("server run completed cleanly");

        let workspace_id = crate::host::workspace_id_for(&root);
        let stored = FileSessionStore::read_session(
            &structure_home,
            &workspace_id,
            &structure_protocol::SessionId::new(session_id.to_string()),
        )
        .expect("the session file is still readable after close");
        assert!(
            stored.events.iter().any(|envelope| matches!(
                envelope.event,
                structure_protocol::Event::SessionSuspended
            )),
            "session/close must persist session.suspend, not session.close (a terminal \
             state session/load or session/resume could never reopen), got {:?}",
            stored.events
        );

        std::fs::remove_dir_all(root).ok();
        std::fs::remove_dir_all(structure_home).ok();
    }

    #[tokio::test]
    async fn session_list_scopes_by_cwd_and_lists_across_workspaces_without_it() {
        let root_a = temp_root("list-a");
        let root_b = temp_root("list-b");
        let structure_home = temp_root("list-home");

        // Two independent connections, sharing one structure_home, each
        // create one session in a different workspace -- session/new never
        // dispatches MessageSend, so an empty scripted-results queue is
        // enough; no prompt is needed to produce a session worth listing.
        for root in [&root_a, &root_b] {
            let state = scripted_state(vec![], LocalRunnerPolicy::coding(), &structure_home);
            let (server, client_channel) = spawn_agent(state);
            let root = root.clone();
            tokio::time::timeout(
                Duration::from_secs(10),
                ClientRole
                    .builder()
                    .name("test-client-create")
                    .connect_with(client_channel, async move |cx| {
                        cx.send_request(AcpInitializeRequest::new(ProtocolVersion::V1))
                            .block_task()
                            .await?;
                        cx.send_request(AcpNewSessionRequest::new(root))
                            .block_task()
                            .await?;
                        Ok(())
                    }),
            )
            .await
            .expect("create round trip did not time out")
            .expect("create round trip succeeded");
            server
                .await
                .expect("server task did not panic")
                .expect("create connection's server run completed cleanly");
        }

        // A third, independent connection does the listing.
        let state = scripted_state(vec![], LocalRunnerPolicy::coding(), &structure_home);
        let (server, client_channel) = spawn_agent(state);
        let (scoped, all) = tokio::time::timeout(Duration::from_secs(10), {
            let root_a = root_a.clone();
            ClientRole.builder().name("test-client-list").connect_with(
                client_channel,
                async move |cx| {
                    cx.send_request(AcpInitializeRequest::new(ProtocolVersion::V1))
                        .block_task()
                        .await?;
                    let scoped = cx
                        .send_request(AcpListSessionsRequest::new().cwd(Some(root_a)))
                        .block_task()
                        .await?;
                    let all = cx
                        .send_request(AcpListSessionsRequest::new())
                        .block_task()
                        .await?;
                    Ok((scoped, all))
                },
            )
        })
        .await
        .expect("list round trip did not time out")
        .expect("list round trip succeeded");

        server
            .await
            .expect("server task did not panic")
            .expect("list connection's server run completed cleanly");

        assert_eq!(
            scoped.sessions.len(),
            1,
            "a cwd-scoped session/list must return exactly the one session in that \
             workspace, got {:?}",
            scoped.sessions
        );
        assert_eq!(scoped.sessions[0].cwd, root_a);
        assert!(
            scoped.sessions[0].updated_at.is_some(),
            "a session with a real file on disk must report updated_at"
        );
        assert_eq!(
            all.sessions.len(),
            2,
            "an unscoped session/list must return sessions from every workspace, got {:?}",
            all.sessions
        );

        std::fs::remove_dir_all(root_a).ok();
        std::fs::remove_dir_all(root_b).ok();
        std::fs::remove_dir_all(structure_home).ok();
    }

    #[tokio::test]
    async fn a_shell_call_is_gated_approved_executed_and_reported_before_the_turn_ends() {
        let root = temp_root("shell-allow");
        let structure_home = temp_root("shell-allow-home");
        let state = scripted_state(
            vec![
                tool_call_result("shell", serde_json::json!({"command": "true"})),
                text_result("done"),
            ],
            acp_tool_policy(),
            &structure_home,
        );
        let (server, client_channel) = spawn_agent(state);
        let updates: Arc<StdMutex<Vec<SessionUpdate>>> = Arc::new(StdMutex::new(Vec::new()));
        let asked = Arc::new(std::sync::atomic::AtomicUsize::new(0));

        let outcome = tokio::time::timeout(Duration::from_secs(10), {
            let updates = updates.clone();
            let asked = asked.clone();
            let root = root.clone();
            ClientRole
                .builder()
                .name("test-client")
                .on_receive_notification(
                    {
                        let updates = updates.clone();
                        async move |notification: SessionNotification, _connection| {
                            updates
                                .lock()
                                .expect("updates lock poisoned")
                                .push(notification.update);
                            Ok(())
                        }
                    },
                    agent_client_protocol::on_receive_notification!(),
                )
                .on_receive_request(
                    async move |request: RequestPermissionRequest,
                                responder: Responder<RequestPermissionResponse>,
                                _connection| {
                        asked.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                        let allow_once = request
                            .options
                            .iter()
                            .find(|option| option.kind == PermissionOptionKind::AllowOnce)
                            .expect("the agent must offer an allow-once option")
                            .option_id
                            .clone();
                        responder.respond(RequestPermissionResponse::new(
                            RequestPermissionOutcome::Selected(SelectedPermissionOutcome::new(
                                allow_once,
                            )),
                        ))
                    },
                    agent_client_protocol::on_receive_request!(),
                )
                .connect_with(client_channel, async move |cx| {
                    cx.send_request(AcpInitializeRequest::new(ProtocolVersion::V1))
                        .block_task()
                        .await?;
                    let new_session = cx
                        .send_request(AcpNewSessionRequest::new(root))
                        .block_task()
                        .await?;
                    let prompt = cx
                        .send_request(AcpPromptRequest::new(
                            new_session.session_id,
                            vec![AcpContentBlock::from("run something")],
                        ))
                        .block_task()
                        .await?;
                    Ok(prompt.stop_reason)
                })
        })
        .await
        .expect("client round trip did not time out")
        .expect("client round trip succeeded");

        assert_eq!(outcome, StopReason::EndTurn);
        assert_eq!(
            asked.load(std::sync::atomic::Ordering::SeqCst),
            1,
            "the agent must actually gate shell through session/request_permission, not just report success"
        );
        {
            // Scoped so the guard is dropped well before `server.await`
            // below: holding a `std::sync::MutexGuard` across an await point
            // is a bug even when nothing else can ever lock it, since it
            // would poison the lock on a panic mid-await.
            let seen = updates.lock().expect("updates lock poisoned");
            assert!(
                seen.iter()
                    .any(|update| matches!(update, SessionUpdate::ToolCall(_))),
                "expected a tool_call announcement, got {seen:?}"
            );
            assert!(
                seen.iter().any(|update| matches!(
                    update,
                    SessionUpdate::ToolCallUpdate(u) if u.fields.status == Some(ToolCallStatus::Completed)
                )),
                "expected the shell call to be reported completed, got {seen:?}"
            );
        }

        server
            .await
            .expect("server task did not panic")
            .expect("server run completed cleanly");
        std::fs::remove_dir_all(root).ok();
        std::fs::remove_dir_all(structure_home).ok();
    }
}
