//! Line-oriented interactive terminal binding for the coding runtime.

use std::collections::BTreeMap;
use std::io::{BufRead, Write};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use arabica_adapters::{FileSessionStore, NewSession};
use arabica_model::{ContentBlock, RuntimeItem, RuntimeRole};
use arabica_protocol::{
    Command, CommandEnvelope, CommandId, Event, EventEnvelope, SessionId, SessionStatus,
    ToolPermissionOutcome, ToolPermissionScope, ToolPermissionSource,
};
use arabica_provider::{
    ApiModelProvider, ApiProviderConfig, BlendProvider, ModelProgress, ModelProgressSink,
};
use arabica_runner::LocalTool;
use arabica_runtime::BlendRoutingPolicy;
use arabica_runtime::{
    PermissionDecision, PermissionRequest, RunCancellation, RunControl, ToolPermissionGate,
    ToolPermissionPolicy, ToolPermissionRule,
};
use arabica_session::{
    DispatchControl, EventVisibility, FanOutObserver, SessionEventObserver, SessionManager,
};

use crate::config::{save_workspace_settings, set_thinking};
use crate::host::{
    HostModel, HostRuntime, LocalRunnerPolicy, ScriptedModel, UuidIds,
    build_host_runtime_with_blend, workspace_id_for,
};
use crate::print::{self, Resume};

pub struct InteractiveOptions {
    pub allow_shell: bool,
    pub read_only: bool,
    pub resume: Resume,
}

struct TerminalObserver {
    state: Mutex<TerminalState>,
}

#[derive(Default)]
struct TerminalState {
    step_message: String,
    step_reasoning: String,
    displayed: String,
    line_open: bool,
    thinking_open: bool,
}

impl TerminalObserver {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            state: Mutex::new(TerminalState::default()),
        })
    }

    fn sink(self: &Arc<Self>) -> ModelProgressSink {
        let observer = Arc::clone(self);
        ModelProgressSink::new(move |progress| {
            let mut state = observer.state.lock().expect("terminal state lock poisoned");
            match progress {
                ModelProgress::Start => {
                    state.step_message.clear();
                    state.step_reasoning.clear();
                }
                ModelProgress::Message(text) => {
                    if state.thinking_open {
                        eprintln!();
                        state.thinking_open = false;
                    }
                    print!("{text}");
                    let _ = std::io::stdout().flush();
                    state.step_message.push_str(&text);
                    state.displayed.push_str(&text);
                    state.line_open = true;
                }
                ModelProgress::Reasoning(text) => {
                    if !state.thinking_open {
                        eprint!("[thinking] ");
                        state.thinking_open = true;
                    }
                    eprint!("{text}");
                    let _ = std::io::stderr().flush();
                    state.step_reasoning.push_str(&text);
                }
            }
        })
    }

    fn break_line(&self) {
        let mut state = self.state.lock().expect("terminal state lock poisoned");
        if state.thinking_open {
            eprintln!();
            state.thinking_open = false;
        }
        if state.line_open {
            println!();
            state.line_open = false;
        }
    }

    fn finish(&self, output: Option<&str>) {
        self.break_line();
        let state = self.state.lock().expect("terminal state lock poisoned");
        if let Some(output) = output.filter(|output| !output.is_empty())
            && !state.displayed.ends_with(output)
        {
            println!("{output}");
        }
    }
}

impl SessionEventObserver for TerminalObserver {
    fn observe(&self, envelope: &EventEnvelope, _visibility: EventVisibility) {
        match &envelope.event {
            Event::ModelResponseItem {
                item: RuntimeItem::Message(message),
                ..
            } if message.role == RuntimeRole::Assistant => {
                let full = message
                    .content
                    .iter()
                    .filter_map(|block| match block {
                        ContentBlock::Text { text } => Some(text.as_str()),
                        _ => None,
                    })
                    .collect::<String>();
                let mut state = self.state.lock().expect("terminal state lock poisoned");
                let extra = full
                    .strip_prefix(&state.step_message)
                    .unwrap_or(&full)
                    .to_owned();
                if !extra.is_empty() {
                    if state.thinking_open {
                        eprintln!();
                        state.thinking_open = false;
                    }
                    print!("{extra}");
                    let _ = std::io::stdout().flush();
                    state.displayed.push_str(&extra);
                    state.line_open = true;
                }
            }
            Event::ModelResponseItem {
                item: RuntimeItem::Reasoning(reasoning),
                ..
            } => {
                let full = reasoning.summary.join("\n\n");
                let mut state = self.state.lock().expect("terminal state lock poisoned");
                let extra = full.strip_prefix(&state.step_reasoning).unwrap_or(&full);
                if !extra.is_empty() {
                    if state.thinking_open {
                        eprintln!("{extra}");
                    } else {
                        eprintln!("[thinking] {extra}");
                    }
                    state.thinking_open = false;
                }
            }
            Event::ToolCallRequested { name, .. } => {
                self.break_line();
                eprintln!("→ {name}");
            }
            Event::ToolCallCompleted { name, is_error, .. } if *is_error => {
                eprintln!("✗ {name} failed")
            }
            _ => {}
        }
    }
}

pub(crate) fn policy() -> ToolPermissionPolicy {
    let mut by_tool = BTreeMap::new();
    for name in [
        "read_file",
        "list_dir",
        "grep",
        "find_files",
        "memory_search",
        "memory_read",
    ] {
        by_tool.insert(name.to_owned(), ToolPermissionRule::Allow);
    }
    ToolPermissionPolicy {
        default: ToolPermissionRule::Ask,
        by_tool,
    }
}

fn input_lines() -> tokio::sync::mpsc::UnboundedReceiver<std::io::Result<String>> {
    let (sender, receiver) = tokio::sync::mpsc::unbounded_channel();
    tokio::task::spawn_blocking(move || {
        let stdin = std::io::stdin();
        let mut input = stdin.lock();
        loop {
            let mut line = String::new();
            match input.read_line(&mut line) {
                Ok(0) => break,
                Ok(_) => {
                    if sender.send(Ok(line)).is_err() {
                        break;
                    }
                }
                Err(error) => {
                    let _ = sender.send(Err(error));
                    break;
                }
            }
        }
    });
    receiver
}

fn prompt(label: &str) {
    print!("{label}");
    let _ = std::io::stdout().flush();
}

pub(crate) fn set_progress(model: &mut HostModel, sink: ModelProgressSink) {
    match model {
        HostModel::Api(_) => {
            let old = std::mem::replace(model, HostModel::Scripted(ScriptedModel::default()));
            if let HostModel::Api(provider) = old {
                *model = HostModel::StreamingApi(provider, sink);
            }
        }
        HostModel::StreamingApi(_, progress) => *progress = sink,
        HostModel::Blend(_) => {}
        HostModel::Scripted(_) => {}
    }
}

pub(crate) struct InteractiveSession {
    pub(crate) manager: SessionManager<HostRuntime>,
    pub(crate) session_id: SessionId,
    pub(crate) store: Arc<FileSessionStore>,
    pub(crate) config: ApiProviderConfig,
    pub(crate) model_configs: BTreeMap<String, ApiProviderConfig>,
    blend_policy: Option<BlendRoutingPolicy>,
    pub(crate) arabica_home: PathBuf,
    pub(crate) runner_root: PathBuf,
    pub(crate) read_only: bool,
    pub(crate) allow_shell: bool,
}

impl InteractiveSession {
    pub(crate) async fn open(
        config: ApiProviderConfig,
        options: InteractiveOptions,
    ) -> Result<Self, Box<dyn std::error::Error>> {
        let model = HostModel::Api(ApiModelProvider::new(config.clone())?);
        Self::open_with_model(config, model, None, options).await
    }

    pub(crate) async fn open_with_model(
        config: ApiProviderConfig,
        model: HostModel,
        blend_policy: Option<BlendRoutingPolicy>,
        options: InteractiveOptions,
    ) -> Result<Self, Box<dyn std::error::Error>> {
        Self::open_with_model_configs(config, model, blend_policy, BTreeMap::new(), options).await
    }

    pub(crate) async fn open_with_model_configs(
        config: ApiProviderConfig,
        model: HostModel,
        blend_policy: Option<BlendRoutingPolicy>,
        model_configs: BTreeMap<String, ApiProviderConfig>,
        options: InteractiveOptions,
    ) -> Result<Self, Box<dyn std::error::Error>> {
        let session_blend_policy = blend_policy.clone();
        let runner_root = std::env::current_dir()?;
        let arabica_home = arabica_adapters::default_arabica_home()?;
        let workspace_id = workspace_id_for(&runner_root);
        let resumed = print::resolve_resume(&options.resume, &arabica_home, &workspace_id)?;
        let mut tool_policy = if options.read_only {
            LocalRunnerPolicy::read_only()
        } else {
            LocalRunnerPolicy::coding()
        };
        if options.allow_shell {
            tool_policy = tool_policy.with_tool(LocalTool::Shell);
        }
        // MCP servers come from the user config file, shared with `-p` and
        // ACP through the same client and permission logic. A broken one is
        // reported and skipped rather than failing the session.
        let (mcp, mcp_diagnostics) = crate::mcp::connect_configured(&runner_root, &arabica_home)
            .await
            .map_err(|error| format!("mcp configuration: {error}"))?;
        for diagnostic in mcp_diagnostics {
            eprintln!("structure: {diagnostic}");
        }
        let mut manager = SessionManager::with_ids(
            build_host_runtime_with_blend(
                model,
                &runner_root,
                tool_policy,
                &arabica_home,
                mcp,
                blend_policy,
            )?,
            Box::new(UuidIds),
        );
        let instructions_sha256 =
            crate::instructions::sha256(manager.runtime().system_instructions());
        let (session_id, store) = match resumed {
            Some(stored) => {
                let session_id = stored.header.id.clone();
                let path =
                    FileSessionStore::session_path(&arabica_home, &workspace_id, &session_id);
                let store = Arc::new(FileSessionStore::open_existing(&path)?);
                manager.restore_session(
                    stored.into_snapshot(),
                    CommandId::new(uuid::Uuid::now_v7().to_string()),
                    Some(store.as_ref()),
                )?;
                if manager
                    .session(&session_id)
                    .is_some_and(|session| session.status == SessionStatus::Suspended)
                {
                    manager
                        .dispatch(
                            CommandEnvelope::new(
                                CommandId::new(uuid::Uuid::now_v7().to_string()),
                                Some(session_id.clone()),
                                Command::SessionResume,
                            ),
                            DispatchControl {
                                run: RunControl::default(),
                                observer: Some(Arc::clone(&store) as Arc<dyn SessionEventObserver>),
                            },
                        )
                        .await?;
                }
                (session_id, store)
            }
            None => {
                let created = manager
                    .dispatch(
                        CommandEnvelope::new(
                            CommandId::new(uuid::Uuid::now_v7().to_string()),
                            None,
                            Command::SessionCreate {
                                workspace_id: workspace_id.clone(),
                            },
                        ),
                        DispatchControl::default(),
                    )
                    .await?;
                let event = created.first().expect("session.create emits an event");
                let session_id = event.session_id.clone();
                let store = Arc::new(FileSessionStore::create(
                    &arabica_home,
                    NewSession {
                        session_id: &session_id,
                        workspace_id: &workspace_id,
                        cwd: &runner_root,
                        profile: None,
                        instructions_sha256: Some(&instructions_sha256),
                    },
                )?);
                store.observe(event, EventVisibility::Client);
                (session_id, store)
            }
        };
        Ok(Self {
            manager,
            session_id,
            store,
            config,
            model_configs,
            blend_policy: session_blend_policy,
            arabica_home,
            runner_root,
            read_only: options.read_only,
            allow_shell: options.allow_shell,
        })
    }

    /// Mark this session suspended in its store so a later `--resume`,
    /// `/resume`, or ACP `session/load` can pick it back up cleanly.
    pub(crate) async fn suspend(&mut self) -> Result<(), Box<dyn std::error::Error>> {
        let envelope = CommandEnvelope::new(
            CommandId::new(uuid::Uuid::now_v7().to_string()),
            Some(self.session_id.clone()),
            Command::SessionSuspend,
        );
        self.manager
            .dispatch(
                envelope,
                DispatchControl {
                    observer: Some(Arc::clone(&self.store) as Arc<dyn SessionEventObserver>),
                    ..DispatchControl::default()
                },
            )
            .await?;
        Ok(())
    }

    /// `/sessions`: the stored sessions for this workspace, formatted the
    /// same way `arabica sessions list` prints them.
    pub(crate) fn session_list(&self) -> Result<Vec<String>, Box<dyn std::error::Error>> {
        Ok(self
            .session_list_entries()?
            .into_iter()
            .map(|entry| entry.line)
            .collect())
    }

    /// The same listings as [`Self::session_list`], keeping each session's
    /// id so `/resume`'s picker can act on the selection.
    pub(crate) fn session_list_entries(
        &self,
    ) -> Result<Vec<crate::sessions::SessionEntry>, Box<dyn std::error::Error>> {
        crate::sessions::listing_entries(
            &self.arabica_home,
            Some(&workspace_id_for(&self.runner_root)),
        )
    }

    /// `/resume <id>`: open `id` in this workspace, then suspend the
    /// session it replaces. The new session is opened before the old one
    /// is suspended, so an unknown id leaves the current session
    /// untouched. On success the caller replaces its session with the
    /// returned one.
    pub(crate) async fn switch_to(
        &mut self,
        id: &str,
    ) -> Result<InteractiveSession, Box<dyn std::error::Error>> {
        let next = InteractiveSession::open(
            self.config.clone(),
            InteractiveOptions {
                allow_shell: self.allow_shell,
                read_only: self.read_only,
                resume: Resume::Id(id.to_owned()),
            },
        )
        .await?;
        self.suspend().await?;
        Ok(next)
    }

    /// Re-read the workspace `AGENTS.md` files so the next turn's model
    /// request reflects edits made since the session opened. Both terminal
    /// surfaces call this before dispatching a turn.
    pub(crate) fn refresh_instructions(&mut self) {
        self.manager
            .runtime_mut()
            .set_system_instructions(crate::host::system_instructions(&self.runner_root));
    }

    /// `/diff`: the agent's net file changes this session, as recorded by
    /// the runner's write checkpoints.
    pub(crate) fn write_report(&self) -> String {
        self.manager
            .runtime()
            .runner()
            .write_journal()
            .lock()
            .expect("write journal lock poisoned")
            .report(&self.runner_root)
    }

    /// `/undo`: revert the most recent agent write call. Refuses -- and
    /// touches nothing -- when a file it changed has moved on on disk, so
    /// user edits are never overwritten.
    pub(crate) fn undo_last_write(&self) -> String {
        let journal = self.manager.runtime().runner().write_journal();
        let mut journal = journal.lock().expect("write journal lock poisoned");
        match journal.undo_last(&self.runner_root) {
            crate::checkpoint::UndoOutcome::NothingToDo => {
                "No agent file changes to undo.".to_owned()
            }
            crate::checkpoint::UndoOutcome::Reverted { tool, paths } => {
                format!("Reverted {tool}: {}", paths.join(", "))
            }
            crate::checkpoint::UndoOutcome::Conflict { path } => format!(
                "{path} changed on disk since the agent last wrote it; nothing was \
                 overwritten. Put the file back to the agent's content, then /undo \
                 again."
            ),
            crate::checkpoint::UndoOutcome::Failed(message) => {
                format!("could not revert: {message}")
            }
        }
    }

    pub(crate) fn change_model(&mut self, model: &str) -> Result<(), Box<dyn std::error::Error>> {
        if !self.model_configs.is_empty() {
            let next = self
                .model_configs
                .get(model)
                .cloned()
                .ok_or_else(|| format!("unknown Blend model alias {model:?}"))?;
            let mut policy = self
                .blend_policy
                .clone()
                .ok_or("Blend model aliases require a routing policy")?;
            policy.default_model = model.to_owned();
            let provider = BlendProvider::from_configs(model, self.model_configs.clone())?;
            save_workspace_settings(&self.arabica_home, &self.runner_root, |settings| {
                settings.model = None;
                settings.model_alias = Some(model.to_owned());
            })?;
            self.config = next;
            self.blend_policy = Some(policy.clone());
            self.manager.runtime_mut().set_blend_policy(Some(policy))?;
            *self.manager.runtime_mut().model_mut() = HostModel::Blend(provider);
            return Ok(());
        }
        let mut next = self.config.clone();
        next.model = model.to_owned();
        let provider = ApiModelProvider::new(next.clone())?;
        save_workspace_settings(&self.arabica_home, &self.runner_root, |settings| {
            settings.model = Some(model.to_owned());
        })?;
        self.config = next;
        *self.manager.runtime_mut().model_mut() = HostModel::Api(provider);
        Ok(())
    }

    pub(crate) fn change_thinking(
        &mut self,
        value: &str,
    ) -> Result<(), Box<dyn std::error::Error>> {
        if !self.model_configs.is_empty() {
            let alias = self
                .blend_policy
                .as_ref()
                .ok_or("Blend model aliases require a routing policy")?
                .default_model
                .clone();
            let mut next = self.model_configs[&alias].clone();
            set_thinking(&mut next, value)?;
            let mut configs = self.model_configs.clone();
            configs.insert(alias.clone(), next.clone());
            let provider = BlendProvider::from_configs(alias, configs.clone())?;
            save_workspace_settings(&self.arabica_home, &self.runner_root, |settings| {
                settings.thinking = Some(value.to_owned());
            })?;
            self.config = next;
            self.model_configs = configs;
            *self.manager.runtime_mut().model_mut() = HostModel::Blend(provider);
            return Ok(());
        }
        let mut next = self.config.clone();
        set_thinking(&mut next, value)?;
        let provider = ApiModelProvider::new(next.clone())?;
        save_workspace_settings(&self.arabica_home, &self.runner_root, |settings| {
            settings.thinking = Some(value.to_owned());
        })?;
        self.config = next;
        *self.manager.runtime_mut().model_mut() = HostModel::Api(provider);
        Ok(())
    }

    async fn turn(
        &mut self,
        text: String,
        lines: &mut tokio::sync::mpsc::UnboundedReceiver<std::io::Result<String>>,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let terminal = TerminalObserver::new();
        self.refresh_instructions();
        set_progress(self.manager.runtime_mut().model_mut(), terminal.sink());
        let observer: Arc<dyn SessionEventObserver> = Arc::new(FanOutObserver::new(vec![
            Arc::clone(&self.store) as Arc<dyn SessionEventObserver>,
            Arc::clone(&terminal) as Arc<dyn SessionEventObserver>,
        ]));
        let cancellation = RunCancellation::new();
        let ctrl_c = tokio::spawn({
            let cancellation = cancellation.clone();
            async move {
                loop {
                    if tokio::signal::ctrl_c().await.is_err() {
                        break;
                    }
                    if cancellation.is_cancelled() {
                        std::process::exit(130);
                    }
                    eprintln!("structure: cancelling the current run...");
                    cancellation.cancel();
                }
            }
        });
        let (permission_tx, mut permission_rx) =
            tokio::sync::mpsc::unbounded_channel::<PermissionRequest>();
        let control = DispatchControl {
            run: RunControl {
                cancellation: Some(cancellation.clone()),
                permissions: Some(ToolPermissionGate {
                    policy: policy(),
                    approver: Some(permission_tx),
                }),
            },
            observer: Some(observer),
        };
        let envelope = CommandEnvelope::new(
            CommandId::new(uuid::Uuid::now_v7().to_string()),
            Some(self.session_id.clone()),
            Command::MessageSend { content: text },
        );
        let dispatch = self.manager.dispatch(envelope, control);
        tokio::pin!(dispatch);
        let events = loop {
            tokio::select! {
                result = &mut dispatch => break result?,
                request = permission_rx.recv() => {
                    let Some(request) = request else { continue; };
                    terminal.break_line();
                    eprintln!("Allow {} {}? [y]es / [a]lways / [n]o / ne[v]er", request.call.name, request.call.arguments);
                    prompt("permission> ");
                    let answer = tokio::select! {
                        line = lines.recv() => line,
                        () = cancellation.cancelled() => None,
                    };
                    let decision = match answer {
                        Some(Ok(line)) => match line.trim().to_ascii_lowercase().as_str() {
                            "y" | "yes" => PermissionDecision::allow_once(),
                            "a" | "always" => PermissionDecision::allow_for_session(),
                            "v" | "never" => PermissionDecision { outcome: ToolPermissionOutcome::Denied, scope: ToolPermissionScope::Session, source: ToolPermissionSource::User },
                            _ => PermissionDecision::deny_once(),
                        },
                        _ => PermissionDecision::deny_once(),
                    };
                    let _ = request.reply.send(decision);
                }
            }
        };
        ctrl_c.abort();
        let mut output = None;
        for event in &events {
            match &event.event {
                Event::RunCompleted { output: answer } => output = answer.as_deref(),
                Event::RunFailed { message } => eprintln!("error: {message}"),
                Event::RunCancelled => eprintln!("structure: cancelled"),
                _ => {}
            }
        }
        terminal.finish(output);
        Ok(())
    }
}

pub async fn run(config: ApiProviderConfig, options: InteractiveOptions) -> i32 {
    let model = match ApiModelProvider::new(config.clone()) {
        Ok(model) => HostModel::Api(model),
        Err(error) => {
            eprintln!("error: {error}");
            return 2;
        }
    };
    run_with_model(config, model, None, options).await
}

pub async fn run_with_model(
    config: ApiProviderConfig,
    model: HostModel,
    blend_policy: Option<BlendRoutingPolicy>,
    options: InteractiveOptions,
) -> i32 {
    run_with_model_configs(config, model, blend_policy, BTreeMap::new(), options).await
}

pub async fn run_with_model_configs(
    config: ApiProviderConfig,
    model: HostModel,
    blend_policy: Option<BlendRoutingPolicy>,
    model_configs: BTreeMap<String, ApiProviderConfig>,
    options: InteractiveOptions,
) -> i32 {
    if options.allow_shell && options.read_only {
        eprintln!("error: --allow-shell and --read-only are mutually exclusive");
        return 2;
    }
    let mut session = match InteractiveSession::open_with_model_configs(
        config,
        model,
        blend_policy,
        model_configs,
        options,
    )
    .await
    {
        Ok(session) => session,
        Err(error) => {
            eprintln!("error: {error}");
            return 2;
        }
    };
    eprintln!(
        "Arabica session {}. Type /help for commands.",
        session.session_id
    );
    let mut lines = input_lines();
    loop {
        prompt("structure> ");
        let received = tokio::select! {
            line = lines.recv() => line,
            interrupt = tokio::signal::ctrl_c() => {
                if interrupt.is_ok() { println!(); return 130; }
                eprintln!("error: interrupt handler failed");
                return 1;
            }
        };
        let line = match received {
            Some(Ok(line)) => line,
            Some(Err(error)) => {
                eprintln!("error: reading input: {error}");
                return 1;
            }
            None => {
                println!();
                return 0;
            }
        };
        let text = line.trim();
        if text.is_empty() {
            continue;
        }
        if matches!(text, "/exit" | "/quit") {
            return 0;
        }
        if text == "/help" {
            println!(
                "/help  /exit  /session  /sessions  /resume <id>  /diff  /undo  /model <name>  /thinking <off|on|low|medium|high>"
            );
            continue;
        }
        if text == "/session" {
            println!(
                "session: {}\nmodel: {}\nprovider: {}\nread only: {}\nshell: {}",
                session.session_id,
                session.config.model,
                session.config.api_type,
                session.read_only,
                session.allow_shell
            );
            continue;
        }
        if text == "/diff" {
            print!("{}", session.write_report());
            continue;
        }
        if text == "/undo" {
            println!("{}", session.undo_last_write());
            continue;
        }
        if text == "/sessions" {
            match session.session_list() {
                Ok(lines) if lines.is_empty() => println!("no sessions found"),
                Ok(lines) => {
                    for line in &lines {
                        println!("{line}");
                    }
                }
                Err(error) => eprintln!("error: {error}"),
            }
            continue;
        }
        if text == "/resume" || text.starts_with("/resume ") {
            let id = text["/resume".len()..].trim();
            let id =
                if id.is_empty() {
                    // `/resume` alone: list resumable sessions and pick one.
                    // A line number, a unique id prefix, or an exact id works;
                    // an empty line cancels.
                    let entries = match session.session_list_entries() {
                        Ok(entries) => entries,
                        Err(error) => {
                            eprintln!("error: {error}");
                            continue;
                        }
                    };
                    if entries.is_empty() {
                        println!("no sessions found");
                        continue;
                    }
                    for (index, entry) in entries.iter().enumerate() {
                        println!("{:>3}. {}", index + 1, entry.line);
                    }
                    prompt("resume: number or id (Enter cancels)> ");
                    let answer = match lines.recv().await {
                        Some(Ok(line)) => line.trim().to_owned(),
                        _ => String::new(),
                    };
                    if answer.is_empty() {
                        continue;
                    }
                    match answer.parse::<usize>().ok().and_then(|number| {
                        number.checked_sub(1).and_then(|index| entries.get(index))
                    }) {
                        Some(entry) => entry.id.clone(),
                        None => {
                            let mut matches = entries
                                .iter()
                                .filter(|entry| entry.id.starts_with(&answer))
                                .collect::<Vec<_>>();
                            if matches.len() > 1 {
                                eprintln!("'{}' matches more than one session", answer);
                                continue;
                            }
                            match matches.pop() {
                                Some(entry) => entry.id.clone(),
                                None => {
                                    eprintln!("no session matches '{}'", answer);
                                    continue;
                                }
                            }
                        }
                    }
                } else {
                    id.to_owned()
                };
            match session.switch_to(&id).await {
                Ok(next) => {
                    session = next;
                    println!("session: {}", session.session_id);
                }
                Err(error) => eprintln!("error: {error}"),
            }
            continue;
        }
        if let Some(model) = text.strip_prefix("/model ") {
            if model.trim().is_empty() {
                eprintln!("usage: /model <name>");
            } else if let Err(error) = session.change_model(model.trim()) {
                eprintln!("error: {error}");
            } else {
                println!("model: {}", session.config.model);
            }
            continue;
        }
        if let Some(level) = text.strip_prefix("/thinking ") {
            if let Err(error) = session.change_thinking(level.trim()) {
                eprintln!("error: {error}");
            } else {
                println!("thinking: {level}");
            }
            continue;
        }
        if text.starts_with('/') {
            eprintln!("unknown command: {text}");
            continue;
        }
        if let Err(error) = session.turn(text.to_owned(), &mut lines).await {
            eprintln!("error: {error}");
            return 1;
        }
    }
}
