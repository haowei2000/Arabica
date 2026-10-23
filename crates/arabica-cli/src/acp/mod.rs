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

use crate::mcp::auth::CredentialBroker;

#[cfg(test)]
mod configuration_tests;
mod content;
mod evaluation;
mod mapping;
mod mcp_credentials;
mod permission;
mod session_metadata;
mod stdio;
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
    NewSessionRequest, NewSessionResponse, PromptCapabilities, PromptRequest, PromptResponse,
    ResumeSessionRequest, ResumeSessionResponse, SessionCapabilities, SessionCloseCapabilities,
    SessionConfigOption, SessionConfigOptionCategory, SessionConfigSelectOption,
    SessionId as AcpSessionId, SessionInfo, SessionListCapabilities, SessionNotification,
    SessionResumeCapabilities, SetSessionConfigOptionRequest, SetSessionConfigOptionResponse,
};
use agent_client_protocol::{
    Agent, Client, ConnectionTo, Error as AcpError, Responder, Result as AcpResult,
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

use crate::config::{ResolvedCliConfig, SOE_POLICY_ID};
use crate::host::{HostModel, HostRuntime, LocalRunnerPolicy, build_host_runtime_with_blend};
#[cfg(test)]
use arabica_provider::{ApiModelProvider, BlendProvider};
use arabica_provider::{ApiProviderConfig, ApiType};

#[derive(Clone)]
struct AcpProviderSettings {
    overrides: crate::host::HostConfigArgs,
    initial: ApiProviderConfig,
    models: Vec<String>,
    aliases: BTreeMap<String, String>,
    model_configs: BTreeMap<String, ApiProviderConfig>,
    blend_policy: Option<BlendRoutingPolicy>,
    blend_policies: BTreeMap<String, BlendRoutingPolicy>,
}

impl AcpProviderSettings {
    fn from_resolved(
        overrides: crate::host::HostConfigArgs,
        resolved: &ResolvedCliConfig,
    ) -> Result<Self, AcpError> {
        let catalog = resolved.model_catalog();
        let blend_policies = resolved.blend_policies.clone();
        if blend_policies.contains_key(SOE_POLICY_ID) {
            return Err(AcpError::invalid_params().data(
                "soe is reserved for the single-model policy in ACP; rename the configured policy",
            ));
        }
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
        Ok(Self {
            overrides,
            initial: provider_config,
            models,
            aliases,
            model_configs,
            blend_policy,
            blend_policies,
        })
    }

    fn configured_for_workspace(&self, home: &Path, cwd: &Path) -> AcpResult<Self> {
        // Named policies use the file's per-model thinking. Invocation and
        // workspace thinking overrides belong only to ACP's SOE policy.
        let mut overrides = self.overrides.clone();
        overrides.thinking = None;
        overrides.model_alias = None;
        if overrides.policy.as_deref() == Some(SOE_POLICY_ID) {
            overrides.policy = None;
        }
        match crate::config::resolve_cli_acp_config(&overrides, home, cwd, |key| {
            std::env::var(key).ok()
        }) {
            Ok(resolved) => Self::from_resolved(self.overrides.clone(), &resolved),
            Err(error) if home.join("config.toml").exists() => {
                Err(AcpError::invalid_params().data(error.to_string()))
            }
            // Tests can supply an in-memory provider catalog without a user file.
            Err(_) => Ok(self.clone()),
        }
    }

    fn for_workspace(&self, home: &Path, cwd: &Path) -> AcpResult<Self> {
        let mut next = self.configured_for_workspace(home, cwd)?;
        let preferences = crate::config::read_workspace_settings(home, cwd)
            .map_err(|error| AcpError::invalid_params().data(error.to_string()))?;
        let policy_id = self
            .overrides
            .policy
            .as_ref()
            .or(preferences.blend_policy.as_ref());
        if next.aliases.is_empty() || policy_id.is_some_and(|id| id == SOE_POLICY_ID) {
            return next.with_soe_preferences(&preferences);
        }
        if let Some(id) = policy_id {
            next.blend_policy = Some(next.blend_policies.get(id).cloned().ok_or_else(|| {
                AcpError::invalid_params().data(format!("unknown Blend policy {id:?}"))
            })?);
        }
        if let Some(policy) = &next.blend_policy {
            next.initial = next
                .model_configs
                .get(&policy.default_model)
                .cloned()
                .ok_or_else(|| {
                    AcpError::invalid_params().data("policy default model is unavailable")
                })?;
        }
        Ok(next)
    }

    fn with_soe_preferences(
        mut self,
        preferences: &crate::config::WorkspaceSettings,
    ) -> AcpResult<Self> {
        if !self.aliases.is_empty() {
            let alias = self
                .overrides
                .model_alias
                .clone()
                .or_else(|| preferences.model_alias.clone())
                .or_else(|| {
                    self.blend_policy
                        .as_ref()
                        .map(|policy| policy.default_model.clone())
                })
                .ok_or_else(|| AcpError::invalid_params().data("SOE model is unavailable"))?;
            if !self.aliases.contains_key(&alias) {
                return Err(
                    AcpError::invalid_params().data(format!("unknown model alias {alias:?}"))
                );
            }
            for (alias, thinking) in &preferences.model_thinking {
                if let Some(config) = self.model_configs.get_mut(alias) {
                    crate::config::set_thinking(config, thinking)
                        .map_err(|error| AcpError::invalid_params().data(error))?;
                }
            }
            let selected = self
                .model_configs
                .get_mut(&alias)
                .ok_or_else(|| AcpError::invalid_params().data("SOE model is unavailable"))?;
            if let Some(model) = self
                .overrides
                .model
                .clone()
                .or_else(|| preferences.model.clone())
            {
                selected.model = model;
            }
            if let Some(thinking) = self.overrides.thinking.as_ref().or_else(|| {
                (!preferences.model_thinking.contains_key(&alias))
                    .then_some(preferences.thinking.as_ref())
                    .flatten()
            }) {
                crate::config::set_thinking(selected, thinking)
                    .map_err(|error| AcpError::invalid_params().data(error))?;
            }
            self.initial = selected.clone();
            self.blend_policy = Some(crate::config::soe_policy(alias));
        } else {
            if let Some(model) = self
                .overrides
                .model
                .clone()
                .or_else(|| preferences.model.clone())
            {
                self.initial.model = model;
            }
            if let Some(thinking) = self
                .overrides
                .thinking
                .as_ref()
                .or(preferences.thinking.as_ref())
            {
                crate::config::set_thinking(&mut self.initial, thinking)
                    .map_err(|error| AcpError::invalid_params().data(error))?;
            }
            self.blend_policy = None;
        }
        Ok(self)
    }

    fn is_soe(&self, policy: Option<&BlendRoutingPolicy>) -> bool {
        self.aliases.is_empty() || policy.is_some_and(|policy| policy.policy_id == SOE_POLICY_ID)
    }

    fn build_model(&self) -> AcpResult<HostModel> {
        crate::host::HostModelCatalog {
            provider: self.initial.clone(),
            models: self.aliases.clone(),
            blend_policy: self.blend_policy.clone(),
            model_configs: self.model_configs.clone(),
        }
        .build_model()
        .map_err(provider_error)
    }

    fn options(
        &self,
        config: &ApiProviderConfig,
        policy: Option<&BlendRoutingPolicy>,
    ) -> Vec<SessionConfigOption> {
        let mut policies = vec![SessionConfigSelectOption::new(SOE_POLICY_ID, "SOE")];
        policies.extend(
            self.blend_policies
                .keys()
                .map(|id| SessionConfigSelectOption::new(id.clone(), id.clone())),
        );
        let selected_policy = match policy {
            Some(policy) if !self.is_soe(Some(policy)) => {
                crate::config::policy_selection_key(&self.blend_policies, policy).to_owned()
            }
            _ => SOE_POLICY_ID.to_owned(),
        };
        let mut options = vec![
            SessionConfigOption::select("blend.policy", "Policy", selected_policy, policies)
                .category(SessionConfigOptionCategory::ModelConfig),
        ];
        if !self.is_soe(policy) {
            return options;
        }
        let selected_model = policy
            .map(|policy| policy.default_model.as_str())
            .unwrap_or(&config.model);
        let models = self
            .models
            .iter()
            .map(|model| {
                SessionConfigSelectOption::new(
                    model.clone(),
                    if model == selected_model && self.aliases.contains_key(model) {
                        format!("{model} · {}", config.model)
                    } else {
                        self.model_configs.get(model).map_or_else(
                            || model.clone(),
                            |config| format!("{model} · {}", config.model),
                        )
                    },
                )
            })
            .collect::<Vec<_>>();
        options.push(
            SessionConfigOption::select("model", "Model", selected_model.to_owned(), models)
                .category(SessionConfigOptionCategory::Model),
        );
        if !matches!(
            config.api_type,
            ApiType::OpenAiChatCompletions | ApiType::OpenAiResponses
        ) {
            return options;
        }
        let levels = crate::config::thinking_levels(config.api_type);
        let current = crate::config::thinking_level(config);
        options.push(
            SessionConfigOption::select(
                "thinking",
                "Thinking",
                current.to_owned(),
                levels
                    .iter()
                    .copied()
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
    permission_policy: arabica_runtime::ToolPermissionPolicy,
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
            permission_policy: permission::default_policy(),
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

    fn with_permission_policy(mut self, policy: arabica_runtime::ToolPermissionPolicy) -> Self {
        self.permission_policy = policy;
        self
    }

    fn config_options(settings: Option<&AcpProviderSettings>) -> Option<Vec<SessionConfigOption>> {
        settings.map(|settings| settings.options(&settings.initial, settings.blend_policy.as_ref()))
    }

    fn build_runtime(
        &self,
        model: HostModel,
        cwd: &Path,
        mcp: crate::mcp::McpTools,
        settings: Option<&AcpProviderSettings>,
    ) -> Result<HostRuntime, AcpError> {
        build_host_runtime_with_blend(
            model,
            cwd,
            self.tool_policy.clone(),
            &self.arabica_home,
            mcp,
            settings.and_then(|settings| settings.blend_policy.clone()),
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

    async fn new_session(
        &self,
        request: NewSessionRequest,
        broker: Option<Arc<dyn CredentialBroker>>,
    ) -> AcpResult<NewSessionResponse> {
        if !request.cwd.is_absolute() {
            return Err(AcpError::invalid_params()
                .data(format!("cwd must be absolute: {}", request.cwd.display())));
        }
        let workspace_id = crate::host::workspace_id_for(&request.cwd);
        let session_settings = self
            .provider_settings
            .as_ref()
            .map(|settings| settings.for_workspace(&self.arabica_home, &request.cwd))
            .transpose()?;
        let model = match &session_settings {
            Some(settings) => settings.build_model()?,
            None => (self.model_factory)()?,
        };
        let mcp = connect_session_mcp(
            request.mcp_servers,
            &request.cwd,
            &self.arabica_home,
            broker,
        )
        .await?;
        let runtime = self.build_runtime(model, &request.cwd, mcp, session_settings.as_ref())?;
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
                    provider_config: session_settings
                        .as_ref()
                        .map(|settings| Mutex::new(settings.initial.clone())),
                    model_configs: session_settings
                        .as_ref()
                        .map(|settings| Mutex::new(settings.model_configs.clone())),
                    blend_policy: session_settings
                        .as_ref()
                        .and_then(|settings| settings.blend_policy.clone())
                        .map(Mutex::new),
                }),
            );
        Ok(NewSessionResponse::new(acp_session_id)
            .config_options(Self::config_options(session_settings.as_ref())))
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

        let session_settings = self
            .provider_settings
            .as_ref()
            .map(|settings| settings.for_workspace(&self.arabica_home, &request.cwd))
            .transpose()?;
        let model = match &session_settings {
            Some(settings) => settings.build_model()?,
            None => (self.model_factory)()?,
        };
        let mcp = connect_session_mcp(
            request.mcp_servers,
            &request.cwd,
            &self.arabica_home,
            Some(Arc::new(mcp_credentials::AcpCredentialBroker::new(
                connection.clone(),
            ))),
        )
        .await?;
        let runtime = self.build_runtime(model, &request.cwd, mcp, session_settings.as_ref())?;
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
        let mut open_calls = mapping::OpenToolCalls::default();
        for envelope in original_events
            .iter()
            .chain(restore_report.repaired_events.iter())
        {
            let mut updates = mapping::updates_for(&envelope.event, &envelope.run_id, &request.cwd);
            updates.extend(open_calls.track(&envelope.event, &envelope.run_id));
            for update in updates {
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
                    provider_config: session_settings
                        .as_ref()
                        .map(|settings| Mutex::new(settings.initial.clone())),
                    model_configs: session_settings
                        .as_ref()
                        .map(|settings| Mutex::new(settings.model_configs.clone())),
                    blend_policy: session_settings
                        .as_ref()
                        .and_then(|settings| settings.blend_policy.clone())
                        .map(Mutex::new),
                }),
            );

        Ok(LoadSessionResponse::new()
            .config_options(Self::config_options(session_settings.as_ref())))
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
        broker: Option<Arc<dyn CredentialBroker>>,
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

        let session_settings = self
            .provider_settings
            .as_ref()
            .map(|settings| settings.for_workspace(&self.arabica_home, &request.cwd))
            .transpose()?;
        let model = match &session_settings {
            Some(settings) => settings.build_model()?,
            None => (self.model_factory)()?,
        };
        let mcp = connect_session_mcp(
            request.mcp_servers,
            &request.cwd,
            &self.arabica_home,
            broker,
        )
        .await?;
        let runtime = self.build_runtime(model, &request.cwd, mcp, session_settings.as_ref())?;
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
                    provider_config: session_settings
                        .as_ref()
                        .map(|settings| Mutex::new(settings.initial.clone())),
                    model_configs: session_settings
                        .as_ref()
                        .map(|settings| Mutex::new(settings.model_configs.clone())),
                    blend_policy: session_settings
                        .as_ref()
                        .and_then(|settings| settings.blend_policy.clone())
                        .map(Mutex::new),
                }),
            );

        Ok(ResumeSessionResponse::new()
            .config_options(Self::config_options(session_settings.as_ref())))
    }

    fn set_config_option(
        &self,
        request: SetSessionConfigOptionRequest,
    ) -> AcpResult<SetSessionConfigOptionResponse> {
        let initial_settings = self.provider_settings.as_ref().ok_or_else(|| {
            AcpError::invalid_request().data("provider configuration is unavailable")
        })?;
        let entry = self.entry(&request.session_id).ok_or_else(|| {
            AcpError::invalid_params().data(format!("unknown session {}", request.session_id))
        })?;
        let settings = initial_settings.configured_for_workspace(&self.arabica_home, &entry.cwd)?;
        let config_lock = entry.provider_config.as_ref().ok_or_else(|| {
            AcpError::invalid_request().data("provider configuration is unavailable")
        })?;
        let mut manager = entry.manager.try_lock().map_err(|_| {
            AcpError::invalid_request().data("a prompt is already in progress for this session")
        })?;
        let mut config = config_lock.lock().expect("provider config lock poisoned");
        let value = request
            .value
            .as_value_id()
            .ok_or_else(|| AcpError::invalid_params().data("expected a select value"))?
            .to_string();
        let config_id = request.config_id.to_string();
        if config_id == "blend.policy" {
            let mut next = if value == SOE_POLICY_ID {
                let preferences =
                    crate::config::read_workspace_settings(&self.arabica_home, &entry.cwd)
                        .map_err(|error| AcpError::invalid_params().data(error.to_string()))?;
                settings.with_soe_preferences(&preferences)?
            } else {
                let policy = settings
                    .blend_policies
                    .get(&value)
                    .cloned()
                    .ok_or_else(|| AcpError::invalid_params().data("unknown Blend policy"))?;
                let mut next = settings;
                next.initial = next
                    .model_configs
                    .get(&policy.default_model)
                    .cloned()
                    .ok_or_else(|| {
                        AcpError::invalid_params().data("policy default model is unavailable")
                    })?;
                next.blend_policy = Some(policy);
                next
            };
            // No-alias API hosts have intrinsic SOE without Blend routing.
            if next.aliases.is_empty() {
                next.blend_policy = None;
            }
            let selected_alias = next
                .blend_policy
                .as_ref()
                .map(|policy| policy.default_model.clone());
            return self.apply_provider_settings(
                &entry,
                manager.runtime_mut(),
                &mut config,
                next,
                |preferences| {
                    preferences.blend_policy = Some(value.clone());
                    if value == SOE_POLICY_ID
                        && let Some(alias) = selected_alias
                    {
                        preferences.model_alias = Some(alias);
                    }
                    // SOE's remembered model and thinking remain available when
                    // visiting a named policy, but do not affect its providers.
                },
            );
        }
        let current_policy = entry
            .blend_policy
            .as_ref()
            .map(|lock| lock.lock().expect("Blend policy lock poisoned").clone());
        if !settings.is_soe(current_policy.as_ref())
            && matches!(config_id.as_str(), "model" | "thinking")
        {
            return Err(AcpError::invalid_params().data(
                "model and thinking can be changed only under SOE; edit the configuration file for named policies",
            ));
        }
        let mut next = settings;
        next.initial = config.clone();
        next.blend_policy = current_policy;
        next.model_configs = entry
            .model_configs
            .as_ref()
            .map(|configs| configs.lock().expect("model configs lock poisoned").clone())
            .unwrap_or_else(|| next.model_configs.clone());
        let mut selected_alias = None;
        match config_id.as_str() {
            "model" if next.aliases.contains_key(&value) => {
                next.initial = next.model_configs.get(&value).cloned().ok_or_else(|| {
                    AcpError::invalid_params().data("selected model is unavailable")
                })?;
                next.blend_policy = Some(crate::config::soe_policy(value.clone()));
                selected_alias = Some(value.clone());
            }
            "model" if next.models.contains(&value) => next.initial.model = value.clone(),
            "thinking" => crate::config::set_thinking(&mut next.initial, &value)
                .map_err(|error| AcpError::invalid_params().data(error))?,
            "model" => {
                return Err(AcpError::invalid_params()
                    .data("model is not a configured model or Blend alias"));
            }
            _ => return Err(AcpError::invalid_params().data("unknown configuration option")),
        }
        let active_alias = next
            .blend_policy
            .as_ref()
            .map(|policy| policy.default_model.clone());
        if let Some(alias) = &active_alias {
            next.model_configs
                .insert(alias.clone(), next.initial.clone());
        }
        let selected_model = next.initial.model.clone();
        self.apply_provider_settings(
            &entry,
            manager.runtime_mut(),
            &mut config,
            next,
            |preferences| {
                preferences.blend_policy = Some(SOE_POLICY_ID.to_owned());
                if config_id == "thinking" {
                    if let Some(alias) = &active_alias {
                        preferences.thinking = None;
                        preferences
                            .model_thinking
                            .insert(alias.clone(), value.clone());
                    } else {
                        preferences.thinking = Some(value.clone());
                    }
                } else if let Some(alias) = &selected_alias {
                    preferences.model = None;
                    preferences.model_alias = Some(alias.clone());
                    preferences.thinking = None;
                } else {
                    preferences.model = Some(selected_model);
                }
            },
        )
    }

    fn apply_provider_settings(
        &self,
        entry: &SessionEntry,
        runtime: &mut HostRuntime,
        config: &mut ApiProviderConfig,
        next: AcpProviderSettings,
        edit_preferences: impl FnOnce(&mut crate::config::WorkspaceSettings),
    ) -> AcpResult<SetSessionConfigOptionResponse> {
        let model = next.build_model()?;
        let previous_policy = entry
            .blend_policy
            .as_ref()
            .map(|lock| lock.lock().expect("Blend policy lock poisoned").clone());
        let previous_model = std::mem::replace(runtime.model_mut(), model);
        if let Err(error) = runtime.set_blend_policy(next.blend_policy.clone()) {
            *runtime.model_mut() = previous_model;
            return Err(runtime_error(error));
        }
        if let Err(error) =
            crate::config::save_workspace_settings(&self.arabica_home, &entry.cwd, edit_preferences)
        {
            *runtime.model_mut() = previous_model;
            runtime
                .set_blend_policy(previous_policy)
                .map_err(runtime_error)?;
            return Err(AcpError::internal_error().data(error.to_string()));
        }
        *config = next.initial.clone();
        if let Some(lock) = &entry.blend_policy
            && let Some(policy) = &next.blend_policy
        {
            *lock.lock().expect("Blend policy lock poisoned") = policy.clone();
        }
        if let Some(configs) = &entry.model_configs {
            *configs.lock().expect("model configs lock poisoned") = next.model_configs.clone();
        }
        Ok(SetSessionConfigOptionResponse::new(
            next.options(config, next.blend_policy.as_ref()),
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
    let settings = AcpProviderSettings::from_resolved(resolved.invocation.clone(), &resolved)?;
    let permission_policy = resolved.permission_policy.clone();
    let model_catalog = resolved.model_catalog();
    let model_factory: Arc<ModelFactory> =
        Arc::new(move || model_catalog.build_model().map_err(provider_error));
    let (transport, stdin_closed) = stdio::transport();
    stdio::serve_until_shutdown(
        serve(
            AcpState::new(model_factory, tool_policy, arabica_home)
                .with_provider_settings(settings)
                .with_permission_policy(permission_policy),
            transport,
        ),
        stdin_closed,
        stdio::SHUTDOWN_GRACE,
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
                                .prompt_capabilities(PromptCapabilities::new().image(true))
                                .load_session(true)
                                .mcp_capabilities(McpCapabilities::new().http(true))
                                .session_capabilities(
                                    SessionCapabilities::new()
                                        .list(SessionListCapabilities::new())
                                        .resume(SessionResumeCapabilities::new())
                                        .close(SessionCloseCapabilities::new()),
                                ),
                        )
                        .agent_info(Implementation::new("arabica", env!("CARGO_PKG_VERSION")))
                        .meta(serde_json::Map::from_iter([
                            (
                                "arabica.evaluation".into(),
                                serde_json::json!({
                                    "schemaVersion": 1,
                                    "get": "_arabica/evaluation/get",
                                    "refresh": "_arabica/evaluation/refresh",
                                    "policyAction": "_arabica/evaluation/policy/action",
                                    "asynchronous": true
                                }),
                            ),
                            (
                                "arabica.mcpAuth".into(),
                                serde_json::json!({
                                    "schemaVersion": 1,
                                    "metaKey": crate::mcp::auth::META_KEY,
                                    "modes": ["broker", "oauth"],
                                    "credential": crate::mcp::auth::CREDENTIAL_METHOD
                                }),
                            ),
                            (
                                "arabica.session".into(),
                                serde_json::json!({
                                    "schemaVersion": 1,
                                    "rename": session_metadata::RENAME_METHOD,
                                    "maxTitleLength": session_metadata::MAX_TITLE_LENGTH
                                }),
                            ),
                        ])),
                )
            },
            agent_client_protocol::on_receive_request!(),
        )
        .on_receive_request(
            {
                let state = state.clone();
                async move |request: NewSessionRequest,
                            responder: Responder<NewSessionResponse>,
                            connection: ConnectionTo<Client>| {
                    // Session setup may ask the client for MCP credentials,
                    // so it runs off the dispatch loop that delivers the reply.
                    let state = state.clone();
                    let broker: Arc<dyn CredentialBroker> = Arc::new(
                        mcp_credentials::AcpCredentialBroker::new(connection.clone()),
                    );
                    connection.spawn(async move {
                        match state.new_session(request, Some(broker)).await {
                            Ok(response) => responder.respond(response),
                            Err(error) => responder.respond_with_error(error),
                        }
                    })
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
                    let state = state.clone();
                    let task_connection = connection.clone();
                    connection.spawn(async move {
                        match state.load_session(request, &task_connection).await {
                            Ok(response) => responder.respond(response),
                            Err(error) => responder.respond_with_error(error),
                        }
                    })
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
                            connection: ConnectionTo<Client>| {
                    let state = state.clone();
                    let broker: Arc<dyn CredentialBroker> = Arc::new(
                        mcp_credentials::AcpCredentialBroker::new(connection.clone()),
                    );
                    connection.spawn(async move {
                        match state.resume_session(request, Some(broker)).await {
                            Ok(response) => responder.respond(response),
                            Err(error) => responder.respond_with_error(error),
                        }
                    })
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
        .on_receive_request(
            {
                let state = state.clone();
                async move |request: session_metadata::RenameSessionRequest,
                            responder: Responder<session_metadata::RenameSessionResponse>,
                            _connection: ConnectionTo<Client>| {
                    match session_metadata::rename(&state, request) {
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
                async move |request: evaluation::GetEvaluationRequest,
                            responder: Responder<serde_json::Value>,
                            _connection: ConnectionTo<Client>| {
                    match evaluation::query(&state, &request.session_id, false) {
                        Ok(view) => responder.respond(view),
                        Err(error) => responder.respond_with_error(error),
                    }
                }
            },
            agent_client_protocol::on_receive_request!(),
        )
        .on_receive_request(
            {
                let state = state.clone();
                async move |request: evaluation::RefreshEvaluationRequest,
                            responder: Responder<serde_json::Value>,
                            _connection: ConnectionTo<Client>| {
                    match evaluation::query(&state, &request.session_id, true) {
                        Ok(view) => responder.respond(view),
                        Err(error) => responder.respond_with_error(error),
                    }
                }
            },
            agent_client_protocol::on_receive_request!(),
        )
        .on_receive_request(
            {
                let state = state.clone();
                async move |request: evaluation::PolicyActionRequest,
                            responder: Responder<serde_json::Value>,
                            _connection: ConnectionTo<Client>| {
                    match evaluation::policy_action(&state, &request).await {
                        Ok(view) => responder.respond(view),
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
    broker: Option<Arc<dyn CredentialBroker>>,
) -> AcpResult<crate::mcp::McpTools> {
    let environment = crate::mcp::auth::AuthEnvironment {
        arabica_home: Some(arabica_home.to_path_buf()),
        broker,
    };
    let client_names: HashSet<String> = client_servers
        .iter()
        .map(crate::mcp::server_name)
        .map(str::to_owned)
        .collect();
    let mut tools = crate::mcp::McpTools::connect(client_servers, cwd, &environment)
        .await
        .map_err(|error| AcpError::invalid_params().data(error))?;
    let configured = crate::config::user_config_mcp(arabica_home)
        .map_err(|error| AcpError::internal_error().data(error.to_string()))?;
    let not_supplied_by_client = configured
        .into_iter()
        .filter(|server| !client_names.contains(crate::mcp::server_name(server)))
        .collect::<Vec<_>>();
    let (extra, diagnostics) =
        crate::mcp::McpTools::connect_lenient(not_supplied_by_client, cwd, &environment).await;
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

    let images = match content::prompt_images(&request.prompt) {
        Ok(images) => images,
        Err(message) => {
            return responder.respond_with_error(AcpError::invalid_params().data(message));
        }
    };
    let content = content::prompt_text(&request.prompt);
    let envelope = CommandEnvelope::new(
        CommandId::new(uuid::Uuid::now_v7().to_string()),
        Some(entry.arabica_session_id.clone()),
        Command::MessageSend { content, images },
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
    let mut observers: Vec<Arc<dyn SessionEventObserver>> = vec![
        Arc::clone(&entry.store) as Arc<dyn SessionEventObserver>,
        acp_observer as Arc<dyn SessionEventObserver>,
    ];
    crate::evaluation::attach(&mut observers, &state.arabica_home);
    let observer: Arc<dyn SessionEventObserver> = Arc::new(FanOutObserver::new(observers));
    let control = DispatchControl {
        run: RunControl {
            cancellation: Some(cancellation),
            permissions: Some(ToolPermissionGate {
                policy: state.permission_policy.clone(),
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
                overrides: Default::default(),
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
                assert_eq!(session.config_options.as_ref().map(Vec::len), Some(3));
                let changed = cx
                    .send_request(SetSessionConfigOptionRequest::new(
                        session.session_id.clone(),
                        "thinking",
                        "on",
                    ))
                    .block_task()
                    .await?;
                assert_eq!(changed.config_options.len(), 3);
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
            overrides: Default::default(),
            initial,
            models: vec!["first-model".to_owned(), "second-model".to_owned()],
            aliases: BTreeMap::new(),
            model_configs: BTreeMap::new(),
            blend_policy: None,
            blend_policies: BTreeMap::new(),
        });
        let session = state
            .new_session(NewSessionRequest::new(root.clone()), None)
            .await
            .unwrap();
        assert_eq!(session.config_options.as_ref().unwrap().len(), 3);
        let response = state
            .set_config_option(SetSessionConfigOptionRequest::new(
                session.session_id.clone(),
                "model",
                "second-model",
            ))
            .unwrap();
        assert_eq!(response.config_options.len(), 3);
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
    async fn acp_soe_model_thinking_survive_named_policy_visits_and_resume() {
        let root = temp_root("blend-config");
        let arabica_home = temp_root("blend-config-home");
        let initial = ApiProviderConfig::new(
            ApiType::OpenAiResponses,
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
            overrides: Default::default(),
            initial,
            models: aliases.keys().cloned().collect(),
            aliases,
            model_configs,
            blend_policy: Some(policy),
            blend_policies,
        });
        let session = state
            .new_session(NewSessionRequest::new(root.clone()), None)
            .await
            .unwrap();
        let options = session.config_options.as_ref().unwrap();
        assert_eq!(options.len(), 1);
        assert_eq!(options[0].id.to_string(), "blend.policy");
        let SessionConfigKind::Select(policy_select) = &options[0].kind else {
            panic!("policy option should be a select");
        };
        assert_eq!(policy_select.current_value.to_string(), "acp-test");
        for (option, value) in [("model", "strong"), ("thinking", "max")] {
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
        assert!(
            crate::config::read_workspace_settings(&arabica_home, &root)
                .unwrap()
                .blend_policy
                .is_none()
        );
        state
            .set_config_option(SetSessionConfigOptionRequest::new(
                session.session_id.clone(),
                "blend.policy",
                SOE_POLICY_ID,
            ))
            .unwrap();
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
        state
            .set_config_option(SetSessionConfigOptionRequest::new(
                session.session_id.clone(),
                "thinking",
                "max",
            ))
            .unwrap();
        state
            .set_config_option(SetSessionConfigOptionRequest::new(
                session.session_id.clone(),
                "model",
                "fast",
            ))
            .unwrap();
        state
            .set_config_option(SetSessionConfigOptionRequest::new(
                session.session_id.clone(),
                "thinking",
                "low",
            ))
            .unwrap();
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
        let saved = crate::config::read_workspace_settings(&arabica_home, &root).unwrap();
        assert_eq!(saved.blend_policy.as_deref(), Some("simple"));
        assert_eq!(saved.model_alias.as_deref(), Some("fast"));
        assert_eq!(saved.model_thinking["strong"], "max");
        assert_eq!(saved.model_thinking["fast"], "low");
        let restored = state
            .new_session(NewSessionRequest::new(root.clone()), None)
            .await
            .unwrap();
        let restored_entry = state.entry(&restored.session_id).unwrap();
        assert_eq!(
            restored_entry
                .blend_policy
                .as_ref()
                .unwrap()
                .lock()
                .unwrap()
                .policy_id,
            "simple"
        );
        assert_eq!(
            restored_entry
                .provider_config
                .as_ref()
                .unwrap()
                .lock()
                .unwrap()
                .reasoning_effort
                .as_deref(),
            None
        );
        let other_root = temp_root("blend-other-workspace");
        let isolated = state
            .new_session(NewSessionRequest::new(other_root.clone()), None)
            .await
            .unwrap();
        let isolated_entry = state.entry(&isolated.session_id).unwrap();
        assert_eq!(
            isolated_entry
                .blend_policy
                .as_ref()
                .unwrap()
                .lock()
                .unwrap()
                .default_model,
            "fast"
        );
        assert!(
            !isolated_entry
                .provider_config
                .as_ref()
                .unwrap()
                .lock()
                .unwrap()
                .thinking_enabled
        );
        let active = restored_entry.manager.lock().await;
        assert!(
            state
                .set_config_option(SetSessionConfigOptionRequest::new(
                    restored.session_id.clone(),
                    "blend.policy",
                    SOE_POLICY_ID,
                ))
                .is_err()
        );
        drop(active);
        state
            .set_config_option(SetSessionConfigOptionRequest::new(
                restored.session_id.clone(),
                "blend.policy",
                SOE_POLICY_ID,
            ))
            .unwrap();
        assert_eq!(
            restored_entry
                .blend_policy
                .as_ref()
                .unwrap()
                .lock()
                .unwrap()
                .default_model,
            "fast"
        );
        assert_eq!(
            crate::config::thinking_level(
                &restored_entry
                    .provider_config
                    .as_ref()
                    .unwrap()
                    .lock()
                    .unwrap()
            ),
            "low"
        );
        state
            .set_config_option(SetSessionConfigOptionRequest::new(
                restored.session_id.clone(),
                "model",
                "strong",
            ))
            .unwrap();
        assert_eq!(
            crate::config::thinking_level(
                &restored_entry
                    .provider_config
                    .as_ref()
                    .unwrap()
                    .lock()
                    .unwrap()
            ),
            "max"
        );
        // Release the test's retained writer before reopening the same session.
        drop(restored_entry);
        state
            .close_session(CloseSessionRequest::new(restored.session_id.clone()))
            .await
            .unwrap();
        let resumed = state
            .resume_session(
                ResumeSessionRequest::new(restored.session_id.clone(), root.clone()),
                None,
            )
            .await
            .unwrap();
        assert_eq!(resumed.config_options.as_ref().unwrap().len(), 3);
        let resumed_entry = state.entry(&restored.session_id).unwrap();
        assert_eq!(
            resumed_entry
                .blend_policy
                .as_ref()
                .unwrap()
                .lock()
                .unwrap()
                .policy_id,
            SOE_POLICY_ID
        );
        assert_eq!(
            resumed_entry
                .blend_policy
                .as_ref()
                .unwrap()
                .lock()
                .unwrap()
                .default_model,
            "strong"
        );
        assert_eq!(
            crate::config::thinking_level(
                &resumed_entry
                    .provider_config
                    .as_ref()
                    .unwrap()
                    .lock()
                    .unwrap()
            ),
            "max"
        );
        std::fs::remove_dir_all(other_root).ok();
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

    fn named_tool_call(call_id: &str, name: &str, arguments: serde_json::Value) -> ModelRunResult {
        let mut result = tool_call_result(name, arguments);
        if let Some(RuntimeResponse { items, .. }) = &mut result.response
            && let Some(RuntimeItem::ToolCall(call)) = items.first_mut()
        {
            call.call_id = call_id.to_owned();
        }
        result
    }

    #[tokio::test]
    async fn broker_credentials_rotate_within_one_acp_session_and_stay_per_binding() {
        use crate::mcp::auth::{BrokerRequest, McpAuth, ProvidedCredential};
        use crate::mcp::auth_tests::{Fixture, Lifecycle};
        use agent_client_protocol::schema::v1::McpServerHttp;

        let fixture = Fixture::start(Lifecycle::Modern, true, &["a-1", "b-1"]).await;
        let root = temp_root("mcp-broker");
        let arabica_home = temp_root("mcp-broker-home");
        let state = scripted_state(
            vec![
                named_tool_call("call-a", "mcp__a__echo", serde_json::json!({"value": "a"})),
                named_tool_call("call-b", "mcp__b__echo", serde_json::json!({"value": "b"})),
                text_result("first turn"),
                named_tool_call(
                    "call-a2",
                    "mcp__a__echo",
                    serde_json::json!({"value": "a-rotated"}),
                ),
                text_result("second turn"),
            ],
            acp_tool_policy(),
            &arabica_home,
        );
        let (server, client_channel) = spawn_agent(state);
        // The platform's view of each account's current token.
        let current = Arc::new(StdMutex::new(HashMap::from([
            ("account-a".to_owned(), "a-1".to_owned()),
            ("account-b".to_owned(), "b-1".to_owned()),
        ])));
        let asked = Arc::new(StdMutex::new(Vec::<(String, String)>::new()));
        let declare = |name: &str, binding: &str| {
            McpServer::Http(
                McpServerHttp::new(name, fixture.url.clone()).meta(
                    McpAuth::Broker {
                        binding: binding.to_owned(),
                    }
                    .to_meta(),
                ),
            )
        };
        let servers = vec![declare("a", "account-a"), declare("b", "account-b")];
        let result = tokio::time::timeout(Duration::from_secs(20), {
            let current = current.clone();
            let asked = asked.clone();
            let root = root.clone();
            let fixture = &fixture;
            ClientRole
                .builder()
                .name("test-client")
                .on_receive_request(
                    {
                        let current = current.clone();
                        async move |request: BrokerRequest,
                                    responder: Responder<ProvidedCredential>,
                                    _connection| {
                            asked.lock().unwrap().push((
                                request.binding.clone(),
                                serde_json::to_value(request.reason)
                                    .unwrap()
                                    .as_str()
                                    .unwrap()
                                    .to_owned(),
                            ));
                            let token = current.lock().unwrap().get(&request.binding).cloned();
                            responder.respond(match token {
                                Some(access_token) => ProvidedCredential::Granted {
                                    access_token,
                                    expires_in_seconds: Some(3600),
                                },
                                None => ProvidedCredential::NeedsAuthorization { message: None },
                            })
                        }
                    },
                    agent_client_protocol::on_receive_request!(),
                )
                .on_receive_request(
                    async move |request: RequestPermissionRequest,
                                responder: Responder<RequestPermissionResponse>,
                                _connection| {
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
                    let advertised = &initialize.meta.as_ref().unwrap()["arabica.mcpAuth"];
                    assert_eq!(advertised["credential"], "_arabica/mcp/credential");
                    assert_eq!(advertised["metaKey"], "arabica.auth");
                    let session = cx
                        .send_request(AcpNewSessionRequest::new(root).mcp_servers(servers))
                        .block_task()
                        .await?;
                    let first = cx
                        .send_request(AcpPromptRequest::new(
                            session.session_id.clone(),
                            vec![AcpContentBlock::from("use both accounts")],
                        ))
                        .block_task()
                        .await?;
                    // The platform rotates account A; the agent session and
                    // its MCP connections stay as they are.
                    fixture.rotate("a-1", "a-2");
                    current
                        .lock()
                        .unwrap()
                        .insert("account-a".to_owned(), "a-2".to_owned());
                    let second = cx
                        .send_request(AcpPromptRequest::new(
                            session.session_id,
                            vec![AcpContentBlock::from("use account A again")],
                        ))
                        .block_task()
                        .await?;
                    Ok((first.stop_reason, second.stop_reason))
                })
        })
        .await
        .expect("broker round trip timed out")
        .expect("broker round trip succeeded");
        assert_eq!(result, (StopReason::EndTurn, StopReason::EndTurn));
        assert_eq!(
            *asked.lock().unwrap(),
            [
                ("account-a".to_owned(), "initial".to_owned()),
                ("account-b".to_owned(), "initial".to_owned()),
                ("account-a".to_owned(), "rejected".to_owned()),
            ]
        );
        let calls = fixture
            .observed()
            .into_iter()
            .filter(|request| request.rpc_method == "tools/call")
            .map(|request| request.authorization.unwrap())
            .collect::<Vec<_>>();
        assert_eq!(
            calls,
            ["Bearer a-1", "Bearer b-1", "Bearer a-1", "Bearer a-2"]
        );
        server.abort();
        std::fs::remove_dir_all(root).ok();
        std::fs::remove_dir_all(arabica_home).ok();
    }

    #[tokio::test]
    async fn evaluation_extensions_work_while_session_manager_is_locked() {
        let root = temp_root("evaluation-client");
        let home = temp_root("evaluation-client-home");
        let state = scripted_state(
            vec![text_result("unused")],
            LocalRunnerPolicy::coding(),
            &home,
        );
        let (server, channel) = spawn_agent(state.clone());
        let outcome = tokio::time::timeout(
            Duration::from_secs(5),
            ClientRole
                .builder()
                .name("evaluation-client")
                .connect_with(channel, async move |cx| {
                    let initialized = cx
                        .send_request(AcpInitializeRequest::new(ProtocolVersion::V1))
                        .block_task()
                        .await?;
                    assert_eq!(
                        initialized.meta.as_ref().unwrap()["arabica.evaluation"]["asynchronous"],
                        true
                    );
                    let session = cx
                        .send_request(AcpNewSessionRequest::new(root))
                        .block_task()
                        .await?;
                    let entry = state.entry(&session.session_id).unwrap();
                    let _guard = entry.manager.lock().await;
                    let view = cx
                        .send_request(evaluation::GetEvaluationRequest {
                            session_id: session.session_id.clone(),
                        })
                        .block_task()
                        .await?;
                    assert_eq!(view["schema_version"], 1);
                    assert!(view["report"].is_null());
                    let refreshed = cx
                        .send_request(evaluation::RefreshEvaluationRequest {
                            session_id: session.session_id,
                        })
                        .block_task()
                        .await?;
                    assert_eq!(refreshed["refresh_accepted"], true);
                    assert!(
                        cx.send_request(evaluation::GetEvaluationRequest {
                            session_id: AcpSessionId::new("unknown")
                        })
                        .block_task()
                        .await
                        .is_err()
                    );
                    Ok(())
                }),
        )
        .await
        .expect("evaluation query blocked on the running session");
        outcome.expect("evaluation extension round trip");
        server.abort();
    }

    #[tokio::test]
    async fn rename_extension_persists_loaded_and_historical_sessions_without_model_calls() {
        let root = temp_root("rename-client");
        let home = temp_root("rename-client-home");
        let state = scripted_state(vec![], LocalRunnerPolicy::coding(), &home);
        let retained = state.clone();
        let (server, channel) = spawn_agent(state);
        let session_id = tokio::time::timeout(
            Duration::from_secs(5),
            ClientRole
                .builder()
                .name("rename-client")
                .connect_with(channel, {
                    let root = root.clone();
                    async move |cx| {
                        let initialized = cx
                            .send_request(AcpInitializeRequest::new(ProtocolVersion::V1))
                            .block_task()
                            .await?;
                        assert_eq!(
                            initialized.meta.as_ref().unwrap()["arabica.session"],
                            serde_json::json!({
                                "schemaVersion":1,
                                "rename":"_arabica/session/rename",
                                "maxTitleLength":200,
                            })
                        );
                        let session = cx
                            .send_request(AcpNewSessionRequest::new(root.clone()))
                            .block_task()
                            .await?;
                        let other = cx
                            .send_request(AcpNewSessionRequest::new(root))
                            .block_task()
                            .await?;
                        let entry = retained.entry(&session.session_id).unwrap();
                        let workspace_id = crate::host::workspace_id_for(&entry.cwd);
                        let before = retained
                            .session_store
                            .read(&workspace_id, &entry.arabica_session_id)
                            .unwrap();
                        let guard = entry.manager.lock().await;
                        let response = cx
                            .send_request(session_metadata::RenameSessionRequest {
                                session_id: session.session_id.clone(),
                                title: "  A useful title  ".to_owned(),
                            })
                            .block_task()
                            .await?;
                        assert_eq!(response.session_id, session.session_id);
                        assert_eq!(response.title, "A useful title");
                        let after = retained
                            .session_store
                            .read(&workspace_id, &entry.arabica_session_id)
                            .unwrap();
                        assert_eq!(after.events, before.events);
                        let mut expected = before.header;
                        expected.title = Some(response.title);
                        assert_eq!(
                            serde_json::to_value(after.header).unwrap(),
                            serde_json::to_value(expected).unwrap()
                        );
                        let listings = cx
                            .send_request(AcpListSessionsRequest::new())
                            .block_task()
                            .await?;
                        assert_eq!(
                            listings
                                .sessions
                                .iter()
                                .find(|item| item.session_id == other.session_id)
                                .unwrap()
                                .title,
                            None
                        );
                        for (session_id, title) in [
                            (session.session_id.clone(), "   "),
                            (AcpSessionId::new("../invalid"), "Title"),
                            (AcpSessionId::new("unknown"), "Title"),
                        ] {
                            assert!(
                                cx.send_request(session_metadata::RenameSessionRequest {
                                    session_id,
                                    title: title.to_owned(),
                                })
                                .block_task()
                                .await
                                .is_err()
                            );
                        }
                        drop(guard);
                        drop(entry);
                        cx.send_request(AcpCloseSessionRequest::new(session.session_id.clone()))
                            .block_task()
                            .await?;
                        Ok(session.session_id)
                    }
                }),
        )
        .await
        .expect("rename must not wait for the session manager")
        .expect("rename round trip succeeds");
        server.await.unwrap().unwrap();

        let factory: Arc<ModelFactory> = Arc::new(|| panic!("renaming must not construct a model"));
        let state = AcpState::new(factory, LocalRunnerPolicy::coding(), home.clone());
        let retained = state.clone();
        let (server, channel) = spawn_agent(state);
        ClientRole
            .builder()
            .name("historical-rename-client")
            .connect_with(channel, async move |cx| {
                cx.send_request(AcpInitializeRequest::new(ProtocolVersion::V1))
                    .block_task()
                    .await?;
                assert!(retained.entry(&session_id).is_none());
                let before = retained.session_store.list(None).unwrap();
                let listing = before
                    .iter()
                    .find(|listing| listing.header.id.to_string() == session_id.to_string())
                    .unwrap();
                let events = retained
                    .session_store
                    .read(&listing.header.workspace_id, &listing.header.id)
                    .unwrap()
                    .events;
                let response = cx
                    .send_request(session_metadata::RenameSessionRequest {
                        session_id: session_id.clone(),
                        title: "Historical title".to_owned(),
                    })
                    .block_task()
                    .await?;
                assert_eq!(response.title, "Historical title");
                assert!(retained.entry(&session_id).is_none());
                let after = retained.session_store.list(None).unwrap();
                let renamed = after
                    .iter()
                    .find(|item| item.header.id == listing.header.id)
                    .unwrap();
                assert_eq!(renamed.header.title.as_deref(), Some("Historical title"));
                assert!(renamed.updated_at_ms >= listing.updated_at_ms);
                assert_eq!(
                    retained
                        .session_store
                        .read(&listing.header.workspace_id, &listing.header.id)
                        .unwrap()
                        .events,
                    events
                );
                Ok(())
            })
            .await
            .expect("historical rename round trip succeeds");
        server.await.unwrap().unwrap();
        let listing = SqliteSessionStore::list_sessions(&home, None)
            .unwrap()
            .into_iter()
            .find(|listing| listing.header.title.as_deref() == Some("Historical title"))
            .unwrap();
        assert_eq!(listing.header.cwd, root.display().to_string());
        std::fs::remove_dir_all(root).ok();
        std::fs::remove_dir_all(home).ok();
    }

    #[tokio::test]
    async fn rename_respects_other_connections_writer_ownership() {
        let root = temp_root("rename-ownership");
        let home = temp_root("rename-ownership-home");
        let owner = scripted_state(vec![], LocalRunnerPolicy::coding(), &home);
        let session = owner
            .new_session(AcpNewSessionRequest::new(root.clone()), None)
            .await
            .unwrap();
        let other = scripted_state(vec![], LocalRunnerPolicy::coding(), &home);
        let request = session_metadata::RenameSessionRequest {
            session_id: session.session_id.clone(),
            title: "Changed elsewhere".to_owned(),
        };
        assert!(session_metadata::rename(&other, request.clone()).is_err());
        assert_eq!(
            owner.session_store.list(None).unwrap()[0].header.title,
            None
        );
        owner
            .close_session(AcpCloseSessionRequest::new(session.session_id))
            .await
            .unwrap();
        assert!(session_metadata::rename(&other, request).is_ok());
        drop(owner);
        drop(other);
        std::fs::remove_dir_all(root).ok();
        std::fs::remove_dir_all(home).ok();
    }

    #[test]
    fn rename_rejects_ambiguous_historical_ids_without_mutating_either_session() {
        let root = temp_root("rename-ambiguous");
        let home = temp_root("rename-ambiguous-home");
        let state = scripted_state(vec![], LocalRunnerPolicy::coding(), &home);
        let session_id = StructureSessionId::new("duplicate-id");
        for id in ["workspace-a", "workspace-b"] {
            state
                .session_store
                .create(NewSession {
                    session_id: &session_id,
                    workspace_id: &arabica_protocol::WorkspaceId::new(id),
                    cwd: &root,
                    profile: None,
                    instructions_sha256: None,
                })
                .unwrap();
        }
        assert!(
            session_metadata::rename(
                &state,
                session_metadata::RenameSessionRequest {
                    session_id: AcpSessionId::new("duplicate-id"),
                    title: "Wrong target".to_owned(),
                },
            )
            .is_err()
        );
        let listings = state.session_store.list(None).unwrap();
        assert_eq!(listings.len(), 2);
        assert!(
            listings
                .iter()
                .all(|listing| listing.header.title.is_none())
        );
        drop(state);
        std::fs::remove_dir_all(root).ok();
        std::fs::remove_dir_all(home).ok();
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
