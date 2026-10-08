//! `arabica acp`: Agent Client Protocol v1 over stdio.
//!
//! See `docs/runtime_core_architecture.md` Appendix B for the decision
//! record and `crates/arabica-cli/src/host.rs` for the composition this
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

use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use agent_client_protocol::schema::ProtocolVersion;
use agent_client_protocol::schema::v1::{
    AgentCapabilities, CancelNotification, CloseSessionRequest, CloseSessionResponse,
    Implementation, InitializeRequest, InitializeResponse, ListSessionsRequest,
    ListSessionsResponse, LoadSessionRequest, LoadSessionResponse, McpCapabilities, McpServer,
    NewSessionRequest, NewSessionResponse, PromptRequest, PromptResponse, ResumeSessionRequest,
    ResumeSessionResponse, SessionCapabilities, SessionCloseCapabilities, SessionConfigOption,
    SessionConfigOptionCategory, SessionConfigSelectOption, SessionId as AcpSessionId, SessionInfo,
    SessionListCapabilities, SessionNotification, SessionResumeCapabilities,
    SetSessionConfigOptionRequest, SetSessionConfigOptionResponse,
};
use agent_client_protocol::{
    Agent, Client, ConnectionTo, Error as AcpError, Responder, Result as AcpResult, Stdio,
};
#[cfg(test)]
use arabica_adapters::SqliteSessionStore;
use arabica_protocol::{
    Command, CommandEnvelope, CommandId, RunId as StructureRunId, SessionId as StructureSessionId,
    SessionStatus,
};
use arabica_runtime::{BlendRoutingPolicy, RunCancellation, RunControl, ToolPermissionGate};
use arabica_session::{
    DispatchControl, EventVisibility, FanOutObserver, IdAllocator, SessionError,
    SessionEventObserver, SessionManager,
};
use arabica_session::{NewSession, SessionStore, SessionWriter};

use crate::config::ResolvedCliConfig;
use crate::host::{HostModel, HostRuntime, LocalRunnerPolicy, build_host_runtime_with_blend};
use arabica_provider::{ApiModelProvider, ApiProviderConfig, ApiType, BlendProvider};

#[derive(Clone)]
struct AcpProviderSettings {
    initial: ApiProviderConfig,
    models: Vec<String>,
    aliases: BTreeMap<String, String>,
    model_configs: BTreeMap<String, ApiProviderConfig>,
    blend_policy: Option<BlendRoutingPolicy>,
    blend_policies: BTreeMap<String, BlendRoutingPolicy>,
}

impl AcpProviderSettings {
    fn options(
        &self,
        config: &ApiProviderConfig,
        policy: Option<&BlendRoutingPolicy>,
    ) -> Vec<SessionConfigOption> {
        let models = self
            .models
            .iter()
            .map(|model| SessionConfigSelectOption::new(model.clone(), model.clone()))
            .collect::<Vec<_>>();
        let selected_model = policy
            .map(|policy| policy.default_model.as_str())
            .unwrap_or(&config.model);
        let mut options = vec![
            SessionConfigOption::select("model", "Model", selected_model.to_owned(), models)
                .category(SessionConfigOptionCategory::Model),
        ];
        if let Some(policy) = policy {
            let policies = self
                .blend_policies
                .keys()
                .map(|id| SessionConfigSelectOption::new(id.clone(), id.clone()))
                .collect::<Vec<_>>();
            if policies.len() > 1 {
                options.push(
                    SessionConfigOption::select(
                        "blend.policy",
                        "Policy",
                        policy.policy_id.clone(),
                        policies,
                    )
                    .category(SessionConfigOptionCategory::ModelConfig),
                );
            }
        }
        if !matches!(
            config.api_type,
            ApiType::OpenAiChatCompletions | ApiType::OpenAiResponses
        ) {
            return options;
        }
        let levels = if config.api_type == ApiType::OpenAiResponses {
            vec!["off", "low", "medium", "high"]
        } else {
            vec!["off", "on"]
        };
        let current = if !config.thinking_enabled {
            "off"
        } else if config.api_type == ApiType::OpenAiResponses {
            config.reasoning_effort.as_deref().unwrap_or("medium")
        } else {
            "on"
        };
        options.push(
            SessionConfigOption::select(
                "thinking",
                "Thinking",
                current.to_owned(),
                levels
                    .into_iter()
                    .map(|level| SessionConfigSelectOption::new(level, level))
                    .collect::<Vec<_>>(),
            )
            .category(SessionConfigOptionCategory::ThoughtLevel),
        );
        options
    }
}

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
    arabica_session_id: StructureSessionId,
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
    store: Arc<dyn SessionWriter>,
    provider_config: Option<Mutex<ApiProviderConfig>>,
    model_configs: Option<Mutex<BTreeMap<String, ApiProviderConfig>>>,
    blend_policy: Option<Mutex<BlendRoutingPolicy>>,
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
    arabica_home: PathBuf,
    session_store: Arc<dyn SessionStore>,
    sessions: Arc<Mutex<HashMap<AcpSessionId, Arc<SessionEntry>>>>,
    provider_settings: Option<Arc<AcpProviderSettings>>,
}

impl AcpState {
    fn new(
        model_factory: Arc<ModelFactory>,
        tool_policy: LocalRunnerPolicy,
        arabica_home: PathBuf,
    ) -> Self {
        Self {
            model_factory,
            tool_policy,
            session_store: crate::host::session_store(&arabica_home),
            arabica_home,
            sessions: Arc::new(Mutex::new(HashMap::new())),
            provider_settings: None,
        }
    }

    fn with_provider_settings(mut self, settings: AcpProviderSettings) -> Self {
        self.provider_settings = Some(Arc::new(settings));
        self
    }

    fn config_options(&self) -> Option<Vec<SessionConfigOption>> {
        self.provider_settings
            .as_ref()
            .map(|settings| settings.options(&settings.initial, settings.blend_policy.as_ref()))
    }

    fn build_runtime(
        &self,
        model: HostModel,
        cwd: &Path,
        mcp: crate::mcp::McpTools,
    ) -> Result<HostRuntime, AcpError> {
        build_host_runtime_with_blend(
            model,
            cwd,
            self.tool_policy.clone(),
            &self.arabica_home,
            mcp,
            self.provider_settings
                .as_ref()
                .and_then(|settings| settings.blend_policy.clone()),
        )
        .map_err(runtime_error)
    }

    fn entry(&self, session_id: &AcpSessionId) -> Option<Arc<SessionEntry>> {
        self.sessions
            .lock()
            .expect("session map lock poisoned")
            .get(session_id)
            .cloned()
    }

    async fn activate_restored_session(
        manager: &mut SessionManager<HostRuntime>,
        session_id: &StructureSessionId,
        store: &Arc<dyn SessionWriter>,
    ) -> AcpResult<()> {
        let status = manager
            .session(session_id)
            .ok_or_else(|| AcpError::internal_error().data("restored session is missing"))?
            .status;
        match status {
            SessionStatus::Active => Ok(()),
            SessionStatus::Suspended => {
                manager
                    .dispatch(
                        CommandEnvelope::new(
                            CommandId::new(uuid::Uuid::now_v7().to_string()),
                            Some(session_id.clone()),
                            Command::SessionResume,
                        ),
                        DispatchControl {
                            run: RunControl::default(),
                            observer: Some(Arc::clone(store) as Arc<dyn SessionEventObserver>),
                        },
                    )
                    .await
                    .map_err(session_error)?;
                Ok(())
            }
            SessionStatus::Closed => Err(AcpError::invalid_params()
                .data(format!("session {session_id} is permanently closed"))),
        }
    }

    async fn new_session(&self, request: NewSessionRequest) -> AcpResult<NewSessionResponse> {
        if !request.cwd.is_absolute() {
            return Err(AcpError::invalid_params()
                .data(format!("cwd must be absolute: {}", request.cwd.display())));
        }
        let workspace_id = crate::host::workspace_id_for(&request.cwd);
        let model = (self.model_factory)()?;
        let mcp =
            connect_session_mcp(request.mcp_servers, &request.cwd, &self.arabica_home).await?;
        let runtime = self.build_runtime(model, &request.cwd, mcp)?;
        let mut manager = SessionManager::with_ids(runtime, Box::new(UuidIds));
        let instructions_sha256 =
            crate::instructions::sha256(manager.runtime().system_instructions());
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
        let arabica_session_id = session_created.session_id.clone();
        let acp_session_id = AcpSessionId::new(arabica_session_id.to_string());

        let store = self
            .session_store
            .create(NewSession {
                session_id: &arabica_session_id,
                workspace_id: &workspace_id,
                cwd: &request.cwd,
                profile: None,
                instructions_sha256: Some(&instructions_sha256),
            })
            .map_err(|error| AcpError::internal_error().data(error.to_string()))?;
        store.observe(session_created, EventVisibility::Client);

        self.sessions
            .lock()
            .expect("session map lock poisoned")
            .insert(
                acp_session_id.clone(),
                Arc::new(SessionEntry {
                    manager: Arc::new(tokio::sync::Mutex::new(manager)),
                    arabica_session_id,
                    cwd: request.cwd,
                    current_run: Mutex::new(None),
                    store,
                    provider_config: self
                        .provider_settings
                        .as_ref()
                        .map(|settings| Mutex::new(settings.initial.clone())),
                    model_configs: self
                        .provider_settings
                        .as_ref()
                        .map(|settings| Mutex::new(settings.model_configs.clone())),
                    blend_policy: self
                        .provider_settings
                        .as_ref()
                        .and_then(|settings| settings.blend_policy.clone())
                        .map(Mutex::new),
                }),
            );
        Ok(NewSessionResponse::new(acp_session_id).config_options(self.config_options()))
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
        let arabica_session_id = StructureSessionId::new(request.session_id.to_string());

        let stored = self
            .session_store
            .read(&workspace_id, &arabica_session_id)
            .map_err(|error| {
                AcpError::invalid_params().data(format!(
                    "cannot load session {}: {error}",
                    request.session_id
                ))
            })?;
        let original_events = stored.events.clone();

        let store = self
            .session_store
            .open(&workspace_id, &arabica_session_id)
            .map_err(|error| AcpError::internal_error().data(error.to_string()))?;

        let model = (self.model_factory)()?;
        let mcp =
            connect_session_mcp(request.mcp_servers, &request.cwd, &self.arabica_home).await?;
        let runtime = self.build_runtime(model, &request.cwd, mcp)?;
        let mut manager = SessionManager::with_ids(runtime, Box::new(UuidIds));
        let restore_report = manager
            .restore_session(
                stored.into_snapshot(),
                CommandId::new(uuid::Uuid::now_v7().to_string()),
                Some(store.as_ref() as &dyn SessionEventObserver),
            )
            .map_err(session_error)?;
        Self::activate_restored_session(&mut manager, &arabica_session_id, &store).await?;

        let acp_session_id = AcpSessionId::new(arabica_session_id.to_string());
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
                    arabica_session_id,
                    cwd: request.cwd,
                    current_run: Mutex::new(None),
                    store,
                    provider_config: self
                        .provider_settings
                        .as_ref()
                        .map(|settings| Mutex::new(settings.initial.clone())),
                    model_configs: self
                        .provider_settings
                        .as_ref()
                        .map(|settings| Mutex::new(settings.model_configs.clone())),
                    blend_policy: self
                        .provider_settings
                        .as_ref()
                        .and_then(|settings| settings.blend_policy.clone())
                        .map(Mutex::new),
                }),
            );

        Ok(LoadSessionResponse::new().config_options(self.config_options()))
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
        let arabica_session_id = StructureSessionId::new(request.session_id.to_string());

        let stored = self
            .session_store
            .read(&workspace_id, &arabica_session_id)
            .map_err(|error| {
                AcpError::invalid_params().data(format!(
                    "cannot resume session {}: {error}",
                    request.session_id
                ))
            })?;

        let store = self
            .session_store
            .open(&workspace_id, &arabica_session_id)
            .map_err(|error| AcpError::internal_error().data(error.to_string()))?;

        let model = (self.model_factory)()?;
        let mcp =
            connect_session_mcp(request.mcp_servers, &request.cwd, &self.arabica_home).await?;
        let runtime = self.build_runtime(model, &request.cwd, mcp)?;
        let mut manager = SessionManager::with_ids(runtime, Box::new(UuidIds));
        manager
            .restore_session(
                stored.into_snapshot(),
                CommandId::new(uuid::Uuid::now_v7().to_string()),
                Some(store.as_ref() as &dyn SessionEventObserver),
            )
            .map_err(session_error)?;
        Self::activate_restored_session(&mut manager, &arabica_session_id, &store).await?;

        let acp_session_id = AcpSessionId::new(arabica_session_id.to_string());
        self.sessions
            .lock()
            .expect("session map lock poisoned")
            .insert(
                acp_session_id,
                Arc::new(SessionEntry {
                    manager: Arc::new(tokio::sync::Mutex::new(manager)),
                    arabica_session_id,
                    cwd: request.cwd,
                    current_run: Mutex::new(None),
                    store,
                    provider_config: self
                        .provider_settings
                        .as_ref()
                        .map(|settings| Mutex::new(settings.initial.clone())),
                    model_configs: self
                        .provider_settings
                        .as_ref()
                        .map(|settings| Mutex::new(settings.model_configs.clone())),
                    blend_policy: self
                        .provider_settings
                        .as_ref()
                        .and_then(|settings| settings.blend_policy.clone())
                        .map(Mutex::new),
                }),
            );

        Ok(ResumeSessionResponse::new().config_options(self.config_options()))
    }

    fn set_config_option(
        &self,
        request: SetSessionConfigOptionRequest,
    ) -> AcpResult<SetSessionConfigOptionResponse> {
        let settings = self.provider_settings.as_ref().ok_or_else(|| {
            AcpError::invalid_request().data("provider configuration is unavailable")
        })?;
        let entry = self.entry(&request.session_id).ok_or_else(|| {
            AcpError::invalid_params().data(format!("unknown session {}", request.session_id))
        })?;
        let config_lock = entry.provider_config.as_ref().ok_or_else(|| {
            AcpError::invalid_request().data("provider configuration is unavailable")
        })?;
        let mut manager = entry.manager.try_lock().map_err(|_| {
            AcpError::invalid_request().data("a prompt is already in progress for this session")
        })?;
        let mut config = config_lock.lock().expect("provider config lock poisoned");
        let mut updated = config.clone();
        let mut session_model_configs = entry
            .model_configs
            .as_ref()
            .map(|configs| configs.lock().expect("model configs lock poisoned").clone())
            .unwrap_or_else(|| settings.model_configs.clone());
        let mut selected_alias = None;
        let value = request
            .value
            .as_value_id()
            .ok_or_else(|| AcpError::invalid_params().data("expected a select value"))?
            .to_string();
        let config_id = request.config_id.to_string();
        if config_id == "blend.policy" {
            let policy = settings
                .blend_policies
                .get(&value)
                .cloned()
                .ok_or_else(|| AcpError::invalid_params().data("unknown Blend policy"))?;
            let next_config = session_model_configs
                .get(&policy.default_model)
                .cloned()
                .ok_or_else(|| {
                    AcpError::invalid_params().data("policy default model is unavailable")
                })?;
            let model = if settings.aliases.is_empty() {
                HostModel::Api(ApiModelProvider::new(next_config.clone()).map_err(provider_error)?)
            } else {
                HostModel::Blend(
                    BlendProvider::from_configs(
                        policy.default_model.clone(),
                        session_model_configs.clone(),
                    )
                    .map_err(provider_error)?,
                )
            };
            manager
                .runtime_mut()
                .set_blend_policy(Some(policy.clone()))
                .map_err(runtime_error)?;
            *manager.runtime_mut().model_mut() = model;
            *config = next_config;
            if let Some(lock) = &entry.blend_policy {
                *lock.lock().expect("Blend policy lock poisoned") = policy.clone();
            }
            if let Some(configs) = &entry.model_configs {
                *configs.lock().expect("model configs lock poisoned") = session_model_configs;
            }
            return Ok(SetSessionConfigOptionResponse::new(
                settings.options(&config, Some(&policy)),
            ));
        }
        match config_id.as_str() {
            "model" if settings.aliases.contains_key(&value) => {
                updated = session_model_configs[&value].clone();
                selected_alias = Some(value.clone());
            }
            "model" if settings.models.contains(&value) => updated.model = value,
            "thinking" => match (updated.api_type, value.as_str()) {
                (_, "off") => {
                    updated.thinking_enabled = false;
                    updated.reasoning_effort = None;
                }
                (ApiType::OpenAiResponses, "low" | "medium" | "high") => {
                    updated.thinking_enabled = true;
                    updated.reasoning_effort = Some(value);
                }
                (ApiType::OpenAiChatCompletions, "on") => {
                    updated.thinking_enabled = true;
                }
                _ => {
                    return Err(AcpError::invalid_params()
                        .data("unsupported thinking level for this provider"));
                }
            },
            "model" => {
                return Err(AcpError::invalid_params()
                    .data("model is not a configured model or Blend alias"));
            }
            _ => return Err(AcpError::invalid_params().data("unknown configuration option")),
        }
        let model = if !settings.aliases.is_empty() {
            let default_alias = selected_alias
                .clone()
                .or_else(|| {
                    entry.blend_policy.as_ref().and_then(|lock| {
                        lock.lock().ok().map(|policy| policy.default_model.clone())
                    })
                })
                .unwrap_or_else(|| {
                    settings
                        .blend_policy
                        .as_ref()
                        .expect("Blend aliases require policy")
                        .default_model
                        .clone()
                });
            session_model_configs.insert(default_alias.clone(), updated.clone());
            HostModel::Blend(
                BlendProvider::from_configs(default_alias, session_model_configs.clone())
                    .map_err(provider_error)?,
            )
        } else {
            HostModel::Api(ApiModelProvider::new(updated.clone()).map_err(provider_error)?)
        };
        if let Some(configs) = &entry.model_configs {
            *configs.lock().expect("model configs lock poisoned") = session_model_configs;
        }
        *manager.runtime_mut().model_mut() = model;
        if let Some(alias) = selected_alias {
            let mut policy = entry
                .blend_policy
                .as_ref()
                .and_then(|lock| lock.lock().ok().map(|policy| policy.clone()))
                .or_else(|| settings.blend_policy.clone())
                .ok_or_else(|| AcpError::invalid_params().data("Blend policy is unavailable"))?;
            policy.default_model = alias;
            manager
                .runtime_mut()
                .set_blend_policy(Some(policy.clone()))
                .map_err(runtime_error)?;
            if let Some(lock) = &entry.blend_policy {
                *lock.lock().expect("blend policy lock poisoned") = policy;
            }
        }
        *config = updated;
        let current_policy = entry
            .blend_policy
            .as_ref()
            .and_then(|lock| lock.lock().ok().map(|policy| policy.clone()));
        Ok(SetSessionConfigOptionResponse::new(
            settings.options(&config, current_policy.as_ref()),
        ))
    }

    /// `session/list`: only reachable once `initialize` has advertised
    /// `agentCapabilities.sessionCapabilities.list` (set unconditionally in
    /// `serve` below, since this handler exists). Synchronous: unlike
    /// `new_session`/`load_session`, nothing here touches a runtime or a
    /// model, only `$ARABICA_HOME` on disk.
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
        // list --all` uses (`SessionStore::list`'s own
        // `workspace_id: Option<&WorkspaceId>` parameter).
        let workspace_id = request
            .cwd
            .as_ref()
            .map(|cwd| crate::host::workspace_id_for(cwd));
        let listings = self
            .session_store
            .list(workspace_id.as_ref())
            .map_err(|error| AcpError::internal_error().data(error.to_string()))?;
        let sessions = listings
            .into_iter()
            .map(|listing| {
                let mut info = SessionInfo::new(
                    AcpSessionId::new(listing.header.id.to_string()),
                    listing.header.cwd,
                );
                if let Some(title) = listing.header.title {
                    info = info.title(title);
                }
                if let Some(modified_at_ms) = listing.updated_at_ms {
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
            Some(entry.arabica_session_id.clone()),
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

fn provider_error(error: arabica_provider::ProviderError) -> AcpError {
    AcpError::internal_error().data(error.to_string())
}

fn runtime_error(error: arabica_runtime::RuntimeError) -> AcpError {
    AcpError::internal_error().data(error.to_string())
}

fn session_error(error: SessionError) -> AcpError {
    match error.code {
        arabica_protocol::ErrorCode::SessionNotFound
        | arabica_protocol::ErrorCode::InvalidCommand
        | arabica_protocol::ErrorCode::InvalidSessionState
        | arabica_protocol::ErrorCode::ProtocolVersionMismatch => {
            AcpError::invalid_params().data(error.message)
        }
        _ => AcpError::internal_error().data(error.message),
    }
}

/// Runs `arabica acp` to completion (until stdin closes). Every prompt
/// turn asks the ACP client for permission through `permission::bridge` and
/// streams progress through `mapping::AcpObserver`; neither ever writes to
/// stdout, which carries only this connection's own JSON-RPC frames.
pub async fn run(resolved: ResolvedCliConfig, tool_policy: LocalRunnerPolicy) -> AcpResult<()> {
    let arabica_home = arabica_adapters::default_arabica_home()
        .map_err(|error| AcpError::internal_error().data(error.to_string()))?;
    let catalog = resolved.model_catalog();
    let blend_policies = resolved.blend_policies.clone();
    let provider_config = catalog.provider.clone();
    let aliases = catalog.models.clone();
    let model_configs = catalog.model_configs.clone();
    let blend_policy = catalog.blend_policy.clone();
    let mut models = aliases.keys().cloned().collect::<Vec<_>>();
    let initial_selection = blend_policy
        .as_ref()
        .map(|policy| policy.default_model.clone());
    if !aliases.is_empty() {
        if initial_selection
            .as_ref()
            .is_none_or(|selected| !aliases.contains_key(selected))
        {
            return Err(AcpError::invalid_params()
                .data("Blend default_model must name a configured model alias"));
        }
    } else if !models.contains(&provider_config.model) {
        models.insert(0, provider_config.model.clone());
    }
    let settings = AcpProviderSettings {
        initial: provider_config.clone(),
        models,
        aliases: aliases.clone(),
        model_configs,
        blend_policy: blend_policy.clone(),
        blend_policies,
    };
    let model_catalog = catalog.clone();
    let model_factory: Arc<ModelFactory> =
        Arc::new(move || model_catalog.build_model().map_err(provider_error));
    serve(
        AcpState::new(model_factory, tool_policy, arabica_home).with_provider_settings(settings),
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
        .name("arabica")
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
                                .mcp_capabilities(McpCapabilities::new().http(true))
                                .session_capabilities(
                                    SessionCapabilities::new()
                                        .list(SessionListCapabilities::new())
                                        .resume(SessionResumeCapabilities::new())
                                        .close(SessionCloseCapabilities::new()),
                                ),
                        )
                        .agent_info(Implementation::new("arabica", env!("CARGO_PKG_VERSION"))),
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
                async move |request: SetSessionConfigOptionRequest,
                            responder: Responder<SetSessionConfigOptionResponse>,
                            _connection: ConnectionTo<Client>| {
                    match state.set_config_option(request) {
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

/// Connect an ACP request's client-supplied MCP servers, then the ones
/// declared in the user config file behind them; when both declare the same
/// name the client's entry wins, because the editor is the more specific
/// configuration. A client-supplied server that fails to connect fails the
/// request (the client asked for it); a user-config server that fails is
/// reported on stderr and skipped, so one broken entry in the config file
/// cannot take the session down.
async fn connect_session_mcp(
    client_servers: Vec<McpServer>,
    cwd: &Path,
    arabica_home: &Path,
) -> AcpResult<crate::mcp::McpTools> {
    let client_names: HashSet<String> = client_servers
        .iter()
        .map(crate::mcp::server_name)
        .map(str::to_owned)
        .collect();
    let mut tools = crate::mcp::McpTools::connect(client_servers, cwd)
        .await
        .map_err(|error| AcpError::invalid_params().data(error))?;
    let configured = crate::config::user_config_mcp(arabica_home)
        .map_err(|error| AcpError::internal_error().data(error.to_string()))?;
    let not_supplied_by_client = configured
        .into_iter()
        .filter(|server| !client_names.contains(crate::mcp::server_name(server)))
        .collect::<Vec<_>>();
    let (extra, diagnostics) =
        crate::mcp::McpTools::connect_lenient(not_supplied_by_client, cwd).await;
    for diagnostic in diagnostics {
        eprintln!("structure: {diagnostic}");
    }
    tools
        .extend(extra)
        .map_err(|error| AcpError::invalid_params().data(error))?;
    Ok(tools)
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
    let Ok(mut guard) = Arc::clone(&entry.manager).try_lock_owned() else {
        return responder.respond_with_error(
            AcpError::invalid_request().data("a prompt is already in progress for this session"),
        );
    };

    let content = content::prompt_text(&request.prompt);
    let envelope = CommandEnvelope::new(
        CommandId::new(uuid::Uuid::now_v7().to_string()),
        Some(entry.arabica_session_id.clone()),
        Command::MessageSend { content },
    );
    let cancellation = RunCancellation::new();
    *entry.current_run.lock().expect("current_run lock poisoned") = Some(cancellation.clone());

    let (permission_tx, permission_rx) = tokio::sync::mpsc::unbounded_channel();
    let acp_observer = Arc::new(mapping::AcpObserver::new(
        connection.clone(),
        request.session_id.clone(),
        entry.cwd.clone(),
    ));
    let progress = acp_observer.progress_sink();
    // Re-read the workspace `AGENTS.md` files so a file edited mid-session
    // reaches this turn's model request, matching terminal chat's per-turn
    // refresh.
    guard
        .runtime_mut()
        .set_system_instructions(crate::host::system_instructions(&entry.cwd));
    let model = guard.runtime_mut().model_mut();
    match model {
        HostModel::Api(_) => {
            let old = std::mem::replace(
                model,
                HostModel::Scripted(crate::host::ScriptedModel::default()),
            );
            if let HostModel::Api(provider) = old {
                *model = HostModel::StreamingApi(provider, progress);
            }
        }
        HostModel::StreamingApi(_, sink) => *sink = progress,
        HostModel::Blend(_) => {}
        HostModel::Scripted(_) => {}
    }
    let observer: Arc<dyn SessionEventObserver> = Arc::new(FanOutObserver::new(vec![
        Arc::clone(&entry.store) as Arc<dyn SessionEventObserver>,
        acp_observer as Arc<dyn SessionEventObserver>,
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
    //! provider errors, a real `arabica acp` binary against a mock HTTP
    //! server) is T9's job; this is the minimum that must work for T7 to be
    //! trustworthy at all.

    use std::path::Path;
    use std::sync::Mutex as StdMutex;
    use std::time::Duration;

    use agent_client_protocol::schema::v1::SessionConfigKind;
    use agent_client_protocol::schema::v1::{
        CloseSessionRequest as AcpCloseSessionRequest, ContentBlock as AcpContentBlock,
        EnvVariable, InitializeRequest as AcpInitializeRequest,
        ListSessionsRequest as AcpListSessionsRequest, LoadSessionRequest as AcpLoadSessionRequest,
        McpServer, McpServerStdio, NewSessionRequest as AcpNewSessionRequest, PermissionOptionKind,
        PromptRequest as AcpPromptRequest, RequestPermissionOutcome, RequestPermissionRequest,
        RequestPermissionResponse, ResumeSessionRequest as AcpResumeSessionRequest,
        SelectedPermissionOutcome, SessionNotification, SessionUpdate, StopReason, ToolCallStatus,
    };
    use agent_client_protocol::{Channel, Client as ClientRole, Responder};
    use arabica_model::{
        FinishReason, MessageItem, RuntimeItem, RuntimeResponse, RuntimeRole, RuntimeUsage,
        ToolCallItem,
    };
    use arabica_provider::{ModelProvider, ModelRunResult};

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
        arabica_home: &Path,
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
        AcpState::new(model_factory, tool_policy, arabica_home.to_path_buf())
    }

    /// The exact policy `main.rs` builds for `arabica acp` (`coding()`
    /// plus `shell`, opted in at the policy layer and gated by the
    /// permission bridge): tests that exercise the shell tool use this so a
    /// policy drift between the real binary and its own tests fails loudly.
    fn acp_tool_policy() -> LocalRunnerPolicy {
        LocalRunnerPolicy::coding().with_tool(arabica_runner::LocalTool::Shell)
    }

    /// Runs the agent side on a background task and returns its `JoinHandle`
    /// alongside the client-role `ConnectionTo` half of an in-memory
    /// channel pair. Callers drive the client through `client`.
    fn spawn_agent(state: AcpState) -> (tokio::task::JoinHandle<AcpResult<()>>, Channel) {
        let (agent_channel, client_channel) = Channel::duplex();
        (tokio::spawn(serve(state, agent_channel)), client_channel)
    }

    #[tokio::test]
    async fn streaming_chat_updates_reach_acp_once() {
        use axum::Json;
        use axum::routing::post;
        use axum::{Router, serve};
        async fn completion(
            Json(body): Json<serde_json::Value>,
        ) -> ([(axum::http::HeaderName, &'static str); 1], String) {
            assert_eq!(body["stream"], true);
            assert_eq!(body["thinking"]["type"], "enabled");
            let chunks = [
                serde_json::json!({"choices":[{"delta":{"reasoning_content":"reason "}}]}),
                serde_json::json!({"choices":[{"delta":{"reasoning_content":"here","content":"hello "}}]}),
                serde_json::json!({"choices":[{"delta":{"content":"there"},"finish_reason":"stop"}]}),
            ];
            let body = chunks
                .iter()
                .map(|chunk| format!("data: {chunk}\n\n"))
                .collect::<String>()
                + "data: [DONE]\n\n";
            (
                [(axum::http::header::CONTENT_TYPE, "text/event-stream")],
                body,
            )
        }
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        tokio::spawn(async move {
            serve(
                listener,
                Router::new().route("/v1/chat/completions", post(completion)),
            )
            .await
            .unwrap();
        });
        let config = ApiProviderConfig::new(
            ApiType::OpenAiChatCompletions,
            "test-key",
            format!("http://{address}/v1"),
            "mock-model",
        );
        let initial = config.clone();
        let factory: Arc<ModelFactory> = Arc::new(move || {
            ApiModelProvider::new(config.clone())
                .map(HostModel::Api)
                .map_err(provider_error)
        });
        let root = temp_root("streaming");
        let home = temp_root("streaming-home");
        let state = AcpState::new(factory, LocalRunnerPolicy::coding(), home.clone())
            .with_provider_settings(AcpProviderSettings {
                initial,
                models: vec!["mock-model".to_owned()],
                aliases: BTreeMap::new(),
                model_configs: BTreeMap::new(),
                blend_policy: None,
                blend_policies: BTreeMap::new(),
            });
        let (server, channel) = spawn_agent(state);
        let updates = Arc::new(StdMutex::new(Vec::<SessionUpdate>::new()));
        let received = Arc::clone(&updates);
        let outcome = ClientRole
            .builder()
            .name("stream-client")
            .on_receive_notification(
                async move |notification: SessionNotification, _connection| {
                    received.lock().unwrap().push(notification.update);
                    Ok(())
                },
                agent_client_protocol::on_receive_notification!(),
            )
            .connect_with(channel, async move |cx| {
                cx.send_request(AcpInitializeRequest::new(ProtocolVersion::V1))
                    .block_task()
                    .await?;
                let session = cx
                    .send_request(AcpNewSessionRequest::new(root.clone()))
                    .block_task()
                    .await?;
                assert_eq!(session.config_options.as_ref().map(Vec::len), Some(2));
                let changed = cx
                    .send_request(SetSessionConfigOptionRequest::new(
                        session.session_id.clone(),
                        "thinking",
                        "on",
                    ))
                    .block_task()
                    .await?;
                assert_eq!(changed.config_options.len(), 2);
                let result = cx
                    .send_request(AcpPromptRequest::new(
                        session.session_id,
                        vec![AcpContentBlock::from("hello")],
                    ))
                    .block_task()
                    .await?;
                Ok(result.stop_reason)
            })
            .await
            .expect("ACP stream succeeds");
        assert_eq!(outcome, StopReason::EndTurn);
        let updates = updates.lock().unwrap();
        let mut message = String::new();
        let mut thought = String::new();
        for update in updates.iter() {
            let target = match update {
                SessionUpdate::AgentMessageChunk(_) => &mut message,
                SessionUpdate::AgentThoughtChunk(_) => &mut thought,
                _ => continue,
            };
            let chunk = match update {
                SessionUpdate::AgentMessageChunk(chunk)
                | SessionUpdate::AgentThoughtChunk(chunk) => chunk,
                _ => unreachable!(),
            };
            if let agent_client_protocol::schema::v1::ContentBlock::Text(text) = &chunk.content {
                target.push_str(&text.text);
            }
        }
        assert_eq!(message, "hello there");
        assert_eq!(thought, "reason here");
        drop(updates);
        server.abort();
        std::fs::remove_dir_all(home).ok();
    }

    #[tokio::test]
    async fn acp_config_switches_model_and_thinking_for_the_next_turn() {
        let root = temp_root("config");
        let arabica_home = temp_root("config-home");
        let initial = ApiProviderConfig::new(
            ApiType::OpenAiChatCompletions,
            "test-key",
            "http://127.0.0.1:9/v1",
            "first-model",
        );
        let factory_config = initial.clone();
        let model_factory: Arc<ModelFactory> = Arc::new(move || {
            ApiModelProvider::new(factory_config.clone())
                .map(HostModel::Api)
                .map_err(provider_error)
        });
        let state = AcpState::new(
            model_factory,
            LocalRunnerPolicy::coding(),
            arabica_home.clone(),
        )
        .with_provider_settings(AcpProviderSettings {
            initial,
            models: vec!["first-model".to_owned(), "second-model".to_owned()],
            aliases: BTreeMap::new(),
            model_configs: BTreeMap::new(),
            blend_policy: None,
            blend_policies: BTreeMap::new(),
        });
        let session = state
            .new_session(NewSessionRequest::new(root.clone()))
            .await
            .unwrap();
        assert_eq!(session.config_options.as_ref().unwrap().len(), 2);
        let response = state
            .set_config_option(SetSessionConfigOptionRequest::new(
                session.session_id.clone(),
                "model",
                "second-model",
            ))
            .unwrap();
        assert_eq!(response.config_options.len(), 2);
        state
            .set_config_option(SetSessionConfigOptionRequest::new(
                session.session_id.clone(),
                "thinking",
                "on",
            ))
            .unwrap();
        let entry = state.entry(&session.session_id).unwrap();
        let manager = entry.manager.lock().await;
        let HostModel::Api(ApiModelProvider::OpenAiChatCompletions(provider)) =
            manager.runtime().model()
        else {
            panic!("expected Chat Completions provider");
        };
        assert_eq!(provider.config().model, "second-model");
        assert!(provider.config().thinking_enabled);
        drop(manager);
        std::fs::remove_dir_all(root).ok();
        std::fs::remove_dir_all(arabica_home).ok();
    }

    #[tokio::test]
    async fn acp_blend_alias_selection_changes_the_session_default_model() {
        let root = temp_root("blend-config");
        let arabica_home = temp_root("blend-config-home");
        let initial = ApiProviderConfig::new(
            ApiType::OpenAiChatCompletions,
            "test-key",
            "http://127.0.0.1:9/v1",
            "model-fast",
        );
        let aliases = BTreeMap::from([
            ("fast".to_owned(), "model-fast".to_owned()),
            ("strong".to_owned(), "model-strong".to_owned()),
        ]);
        let model_configs = aliases
            .iter()
            .map(|(alias, model_id)| {
                let mut config = initial.clone();
                config.model = model_id.clone();
                (alias.clone(), config)
            })
            .collect::<BTreeMap<_, _>>();
        let policy = BlendRoutingPolicy {
            policy_id: "acp-test".to_owned(),
            version: 1,
            default_model: "fast".to_owned(),
            after_tool_success: Some("fast".to_owned()),
            after_tool_error: Some("strong".to_owned()),
            recovery_model: Some("strong".to_owned()),
            planning_model: None,
            tool_routes: Vec::new(),
            recovery_after_no_progress_steps: 2,
            minimum_model_dwell_steps: 1,
            tool_call_capable_models: Default::default(),
            typed_completion_capable_models: Default::default(),
        };
        let factory_config = initial.clone();
        let factory_aliases = aliases.clone();
        let factory_policy = policy.clone();
        let model_factory: Arc<ModelFactory> = Arc::new(move || {
            BlendProvider::from_shared_config(
                factory_config.clone(),
                &factory_policy.default_model,
                factory_aliases.clone(),
            )
            .map(HostModel::Blend)
            .map_err(provider_error)
        });
        let alternate_policy = BlendRoutingPolicy {
            policy_id: "simple".to_owned(),
            version: 1,
            default_model: "strong".to_owned(),
            after_tool_success: None,
            after_tool_error: None,
            recovery_model: None,
            planning_model: None,
            tool_routes: Vec::new(),
            recovery_after_no_progress_steps: 2,
            minimum_model_dwell_steps: 1,
            tool_call_capable_models: Default::default(),
            typed_completion_capable_models: Default::default(),
        };
        let blend_policies = BTreeMap::from([
            (policy.policy_id.clone(), policy.clone()),
            (alternate_policy.policy_id.clone(), alternate_policy),
        ]);
        let state = AcpState::new(
            model_factory,
            LocalRunnerPolicy::coding(),
            arabica_home.clone(),
        )
        .with_provider_settings(AcpProviderSettings {
            initial,
            models: aliases.keys().cloned().collect(),
            aliases,
            model_configs,
            blend_policy: Some(policy),
            blend_policies,
        });
        let session = state
            .new_session(NewSessionRequest::new(root.clone()))
            .await
            .unwrap();
        let model_option = session
            .config_options
            .as_ref()
            .unwrap()
            .iter()
            .find(|option| option.id.to_string() == "model")
            .unwrap();
        let SessionConfigKind::Select(model_select) = &model_option.kind else {
            panic!("model option should be a select");
        };
        assert_eq!(model_select.current_value.to_string(), "fast");
        let policy_option = session
            .config_options
            .as_ref()
            .unwrap()
            .iter()
            .find(|option| option.id.to_string() == "blend.policy")
            .unwrap();
        let SessionConfigKind::Select(policy_select) = &policy_option.kind else {
            panic!("policy option should be a select");
        };
        assert_eq!(policy_select.current_value.to_string(), "acp-test");
        let response = state
            .set_config_option(SetSessionConfigOptionRequest::new(
                session.session_id.clone(),
                "model",
                "strong",
            ))
            .unwrap();
        let updated_option = response
            .config_options
            .iter()
            .find(|option| option.id.to_string() == "model")
            .unwrap();
        let SessionConfigKind::Select(updated_select) = &updated_option.kind else {
            panic!("model option should be a select");
        };
        assert_eq!(updated_select.current_value.to_string(), "strong");
        let entry = state.entry(&session.session_id).unwrap();
        let policy_response = state
            .set_config_option(SetSessionConfigOptionRequest::new(
                session.session_id.clone(),
                "blend.policy",
                "simple",
            ))
            .unwrap();
        let updated_policy_option = policy_response
            .config_options
            .iter()
            .find(|option| option.id.to_string() == "blend.policy")
            .unwrap();
        let SessionConfigKind::Select(updated_policy_select) = &updated_policy_option.kind else {
            panic!("policy option should be a select");
        };
        assert_eq!(updated_policy_select.current_value.to_string(), "simple");
        {
            let policy = entry.blend_policy.as_ref().unwrap().lock().unwrap();
            assert_eq!(policy.policy_id, "simple");
            assert_eq!(policy.default_model, "strong");
            assert_eq!(policy.after_tool_error, None);
        }
        let manager = entry.manager.lock().await;
        assert_eq!(manager.runtime().model().model_id(), Some("model-strong"));
        assert!(manager.runtime().model().supports_model_alias("fast"));
        assert_eq!(
            entry
                .blend_policy
                .as_ref()
                .unwrap()
                .lock()
                .unwrap()
                .default_model,
            "strong"
        );
        drop(manager);
        std::fs::remove_dir_all(root).ok();
        std::fs::remove_dir_all(arabica_home).ok();
    }

    #[tokio::test]
    async fn client_supplied_stdio_mcp_tool_is_discovered_approved_and_executed() {
        let fixture =
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/mcp_server.rs");
        let root = temp_root("mcp-tool");
        let server_binary = root.join(format!("mcp-server{}", std::env::consts::EXE_SUFFIX));
        let compile = std::process::Command::new("rustc")
            .args(["--edition=2024", "-o"])
            .arg(&server_binary)
            .arg(&fixture)
            .output()
            .expect("rustc is available to compile the MCP fixture");
        assert!(
            compile.status.success(),
            "MCP fixture compilation failed: {}",
            String::from_utf8_lossy(&compile.stderr)
        );
        let marker = root.join("mcp-result.txt");
        let arabica_home = temp_root("mcp-tool-home");
        let state = scripted_state(
            vec![
                tool_call_result("mcp__fixture__echo", serde_json::json!({"value": "worked"})),
                text_result("done"),
            ],
            acp_tool_policy(),
            &arabica_home,
        );
        let (server, client_channel) = spawn_agent(state);
        let asked = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let mcp_server = McpServer::Stdio(McpServerStdio::new("fixture", server_binary).env(vec![
            EnvVariable::new("MCP_TEST_MARKER", marker.display().to_string()),
        ]));
        let outcome = tokio::time::timeout(Duration::from_secs(20), {
            let asked = asked.clone();
            let root = root.clone();
            ClientRole
                .builder()
                .name("test-client")
                .on_receive_request(
                    async move |request: RequestPermissionRequest,
                                responder: Responder<RequestPermissionResponse>,
                                _connection| {
                        asked.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                        let allow_once = request
                            .options
                            .iter()
                            .find(|option| option.kind == PermissionOptionKind::AllowOnce)
                            .expect("allow once is offered")
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
                    let initialize = cx
                        .send_request(AcpInitializeRequest::new(ProtocolVersion::V1))
                        .block_task()
                        .await?;
                    assert!(initialize.agent_capabilities.mcp_capabilities.http);
                    let session = cx
                        .send_request(AcpNewSessionRequest::new(root).mcp_servers(vec![mcp_server]))
                        .block_task()
                        .await?;
                    let prompt = cx
                        .send_request(AcpPromptRequest::new(
                            session.session_id,
                            vec![AcpContentBlock::from("echo worked")],
                        ))
                        .block_task()
                        .await?;
                    Ok(prompt.stop_reason)
                })
        })
        .await
        .expect("MCP round trip timed out")
        .expect("MCP round trip succeeded");
        assert_eq!(outcome, StopReason::EndTurn);
        assert_eq!(asked.load(std::sync::atomic::Ordering::SeqCst), 1);
        assert_eq!(
            std::fs::read_to_string(&marker).expect("MCP tool ran"),
            "worked"
        );
        server.abort();
        std::fs::remove_dir_all(root).ok();
        std::fs::remove_dir_all(arabica_home).ok();
    }

    #[tokio::test]
    async fn a_plain_text_prompt_ends_the_turn_and_streams_the_message() {
        let root = temp_root("plain-text");
        let arabica_home = temp_root("plain-text-home");
        let state = scripted_state(
            vec![text_result("hello from structure")],
            LocalRunnerPolicy::coding(),
            &arabica_home,
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
        std::fs::remove_dir_all(arabica_home).ok();
    }

    #[tokio::test]
    async fn a_prompt_turn_persists_its_session_to_disk() {
        let root = temp_root("persist");
        let arabica_home = temp_root("persist-home");
        let state = scripted_state(
            vec![text_result("hello from structure")],
            LocalRunnerPolicy::coding(),
            &arabica_home,
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

        // The ACP session id is exactly the stringified Arabica session
        // id (`AcpState::new_session`), so this round-trips it back rather
        // than re-deriving anything the store itself would not have used.
        let workspace_id = crate::host::workspace_id_for(&root);
        let stored = SqliteSessionStore::read_session(
            &arabica_home,
            &workspace_id,
            &arabica_protocol::SessionId::new(session_id.to_string()),
        )
        .expect("the session persisted by the live observer is readable back");
        assert_eq!(stored.header.id.to_string(), session_id.to_string());
        assert!(
            matches!(
                stored.events.first().map(|envelope| &envelope.event),
                Some(arabica_protocol::Event::SessionCreated { .. })
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
        std::fs::remove_dir_all(arabica_home).ok();
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
        let arabica_home = temp_root("load-home");

        // Connection 1: create a session and send one prompt, then let the
        // connection end -- simulating the editor (or the agent process)
        // disconnecting. Nothing here ever calls session/load.
        let state1 = scripted_state(
            vec![text_result("hello from turn one")],
            LocalRunnerPolicy::coding(),
            &arabica_home,
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
        // `arabica acp` process would have -- pointed at the SAME
        // arabica_home. session/load must find the session on disk,
        // replay its history as session/update notifications, then accept a
        // new prompt on the session it just restored.
        let state2 = scripted_state(
            vec![text_result("hello again after loading")],
            LocalRunnerPolicy::coding(),
            &arabica_home,
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
        std::fs::remove_dir_all(arabica_home).ok();
    }

    #[tokio::test]
    async fn session_resume_does_not_replay_history_but_does_accept_a_new_prompt() {
        let root = temp_root("resume");
        let arabica_home = temp_root("resume-home");

        // Connection 1: same as session/load's own test -- create a
        // session, send one prompt, let the connection end.
        let state1 = scripted_state(
            vec![text_result("hello from turn one")],
            LocalRunnerPolicy::coding(),
            &arabica_home,
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
        // SqliteSessionStore causing spurious lock contention) resumes the
        // session and sends a new prompt. Unlike session/load, no
        // session/update notifications should arrive before the prompt: the
        // whole point of session/resume is skipping that replay.
        let state2 = scripted_state(
            vec![text_result("hello again after resuming")],
            LocalRunnerPolicy::coding(),
            &arabica_home,
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
        std::fs::remove_dir_all(arabica_home).ok();
    }

    #[tokio::test]
    async fn session_close_suspends_and_forgets_the_session_in_this_connection() {
        let root = temp_root("close");
        let arabica_home = temp_root("close-home");
        let model_factory: Arc<ModelFactory> = Arc::new(|| {
            Ok(HostModel::Scripted(ScriptedModel::new([text_result(
                "hello after reopening",
            )])))
        });
        let state = AcpState::new(
            model_factory,
            LocalRunnerPolicy::coding(),
            arabica_home.clone(),
        );
        let (server, client_channel) = spawn_agent(state);

        let (session_id, prompt_after_close_was_rejected, loaded_stop, resumed_stop) =
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
                        // stale entry would keep its Arc<dyn SessionWriter>
                        // (and the exclusive flock that comes with it) alive
                        // forever, so session/load's own
                        // SqliteSessionStore::open_existing for the SAME
                        // session id would fail on lock contention with that
                        // never-released handle. Success here means no
                        // stale entry survived close.
                        cx.send_request(AcpLoadSessionRequest::new(
                            new_session.session_id.clone(),
                            root.clone(),
                        ))
                        .block_task()
                        .await?;
                        let loaded_stop = cx
                            .send_request(AcpPromptRequest::new(
                                new_session.session_id.clone(),
                                vec![AcpContentBlock::from("after load")],
                            ))
                            .block_task()
                            .await?
                            .stop_reason;
                        cx.send_request(AcpCloseSessionRequest::new(
                            new_session.session_id.clone(),
                        ))
                        .block_task()
                        .await?;
                        cx.send_request(AcpResumeSessionRequest::new(
                            new_session.session_id.clone(),
                            root,
                        ))
                        .block_task()
                        .await?;
                        let resumed_stop = cx
                            .send_request(AcpPromptRequest::new(
                                new_session.session_id.clone(),
                                vec![AcpContentBlock::from("after resume")],
                            ))
                            .block_task()
                            .await?
                            .stop_reason;
                        Ok((
                            new_session.session_id,
                            prompt_rejected,
                            loaded_stop,
                            resumed_stop,
                        ))
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
        assert_eq!(loaded_stop, StopReason::EndTurn);
        assert_eq!(resumed_stop, StopReason::EndTurn);

        server
            .await
            .expect("server task did not panic")
            .expect("server run completed cleanly");

        let workspace_id = crate::host::workspace_id_for(&root);
        let stored = SqliteSessionStore::read_session(
            &arabica_home,
            &workspace_id,
            &arabica_protocol::SessionId::new(session_id.to_string()),
        )
        .expect("the session file is still readable after close");
        assert!(
            stored.events.iter().any(|envelope| matches!(
                envelope.event,
                arabica_protocol::Event::SessionSuspended
            )),
            "session/close must persist session.suspend, not session.close (a terminal \
             state session/load or session/resume could never reopen), got {:?}",
            stored.events
        );
        assert_eq!(
            stored
                .events
                .iter()
                .filter(|envelope| matches!(
                    envelope.event,
                    arabica_protocol::Event::SessionResumed
                ))
                .count(),
            2,
            "both ACP load and resume must activate a suspended session"
        );

        std::fs::remove_dir_all(root).ok();
        std::fs::remove_dir_all(arabica_home).ok();
    }

    #[tokio::test]
    async fn session_list_scopes_by_cwd_and_lists_across_workspaces_without_it() {
        let root_a = temp_root("list-a");
        let root_b = temp_root("list-b");
        let arabica_home = temp_root("list-home");

        // Two independent connections, sharing one arabica_home, each
        // create one session in a different workspace -- session/new never
        // dispatches MessageSend, so an empty scripted-results queue is
        // enough; no prompt is needed to produce a session worth listing.
        for root in [&root_a, &root_b] {
            let state = scripted_state(vec![], LocalRunnerPolicy::coding(), &arabica_home);
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

        for listing in SqliteSessionStore::list_sessions(&arabica_home, None).unwrap() {
            SqliteSessionStore::open_existing(&listing.path)
                .unwrap()
                .set_title("Persisted title")
                .unwrap();
        }

        // A third, independent connection does the listing.
        let state = scripted_state(vec![], LocalRunnerPolicy::coding(), &arabica_home);
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
        assert_eq!(scoped.sessions[0].title.as_deref(), Some("Persisted title"));
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
        std::fs::remove_dir_all(arabica_home).ok();
    }

    #[tokio::test]
    async fn a_shell_call_is_gated_approved_executed_and_reported_before_the_turn_ends() {
        let root = temp_root("shell-allow");
        let arabica_home = temp_root("shell-allow-home");
        let state = scripted_state(
            vec![
                tool_call_result("shell", serde_json::json!({"command": "true"})),
                text_result("done"),
            ],
            acp_tool_policy(),
            &arabica_home,
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
        std::fs::remove_dir_all(arabica_home).ok();
    }
}
