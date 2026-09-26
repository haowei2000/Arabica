//! `structure -p`: run one task non-interactively and exit.
//!
//! Unlike `arabica acp` (`crates/arabica-cli/src/acp/`), there is no
//! client on the other end to ask for permission, so this binding never
//! wires a permission gate at all: what a tool policy admits, it runs
//! outright, and what it excludes is invisible to the model. `--allow-shell`
//! and `--read-only` are the only controls, decided once at startup, not
//! per call. See `docs/runtime_core_architecture.md` Appendix B.
//!
//! Every run persists to `$ARABICA_HOME` (`arabica_adapters::FileSessionStore`)
//! so a later `--continue`/`--resume` has something to pick back up --
//! `arabica sessions list` (`crates/arabica-cli/src/sessions.rs`) reads
//! the same files to show what is available.

use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use arabica_adapters::{FileSessionStore, NewSession, StoredSession};
use arabica_protocol::{
    Command, CommandEnvelope, CommandId, Event, EventEnvelope, SessionStatus, WorkspaceId,
};
use arabica_provider::{ApiModelProvider, ApiProviderConfig};
use arabica_runner::LocalTool;
use arabica_runtime::{RunCancellation, RunControl};
use arabica_session::{
    DispatchControl, EventVisibility, FanOutObserver, SessionEventObserver, SessionManager,
};

use crate::host::{
    HostModel, HostRuntime, LocalRunnerPolicy, build_host_runtime_with_mcp, workspace_id_for,
};

/// stdout's shape in `-p` mode.
#[derive(clap::ValueEnum, Clone, Copy, Debug, Default, Eq, PartialEq)]
#[value(rename_all = "lower")]
pub enum OutputFormat {
    /// stdout carries only the final answer; progress goes to stderr.
    #[default]
    Text,
    /// stdout carries one JSON `EventEnvelope` per line, live, for every
    /// client-visible Event -- the same distinction
    /// `arabica_protocol::Event::is_client_visible` already draws, so
    /// nothing provider-internal (system prompts, raw reasoning) ever
    /// reaches a pipe built for scripting.
    Jsonl,
}

pub struct PrintOptions {
    /// The task text, already resolved: the `-p` value, or stdin's full
    /// content if the value was `-`.
    pub task: String,
    pub output_format: OutputFormat,
    pub allow_shell: bool,
    pub read_only: bool,
    pub resume: Resume,
}

/// Which session `-p` should send `task` to.
pub enum Resume {
    /// Start a fresh session, as `-p` always did before persistence existed.
    None,
    /// `--continue`: the most recently active session in this workspace.
    Continue,
    /// `--resume <ID>`: a specific session id, looked up in this workspace.
    Id(String),
}

/// Reads the task text for `-p`: the flag's value verbatim, or all of
/// stdin (trimmed of its trailing newline) when the value is exactly `-`.
pub fn resolve_task(value: &str) -> Result<String, std::io::Error> {
    if value != "-" {
        return Ok(value.to_owned());
    }
    let mut task = String::new();
    std::io::stdin().read_to_string(&mut task)?;
    Ok(task.trim_end_matches('\n').to_owned())
}

/// The tool policy `-p` builds from its flags. `--read-only` and
/// `--allow-shell` are mutually exclusive: shell has no path confinement,
/// so allowing it while also promising "nothing changes" would make the
/// promise false, not just permissive.
pub fn tool_policy(allow_shell: bool, read_only: bool) -> Result<LocalRunnerPolicy, String> {
    match (allow_shell, read_only) {
        (true, true) => Err("--allow-shell and --read-only are mutually exclusive".to_owned()),
        (true, false) => Ok(LocalRunnerPolicy::coding().with_tool(LocalTool::Shell)),
        (false, true) => Ok(LocalRunnerPolicy::read_only()),
        (false, false) => Ok(LocalRunnerPolicy::coding()),
    }
}

/// Exit codes, matching the plan: 0 completed, 1 failed, 2 configuration
/// error, 130 cancelled (128 + SIGINT, the usual shell convention).
const EXIT_COMPLETED: i32 = 0;
const EXIT_FAILED: i32 = 1;
const EXIT_CONFIG_ERROR: i32 = 2;
const EXIT_CANCELLED: i32 = 130;

enum Outcome {
    Completed(Option<String>),
    Cancelled,
    Failed(String),
}

/// Classify one `message.send` call's Events. Deliberately coarser than
/// `acp::stop_reason::classify`: `-p` has one generic failure exit code, not
/// ACP's five-way `StopReason`, so there is no typed-evidence distinction to
/// make here.
fn classify(events: &[EventEnvelope]) -> Outcome {
    if events
        .iter()
        .any(|envelope| matches!(envelope.event, Event::RunCancelled))
    {
        return Outcome::Cancelled;
    }
    for envelope in events {
        match &envelope.event {
            Event::RunCompleted { output } => return Outcome::Completed(output.clone()),
            Event::RunFailed { message } => return Outcome::Failed(message.clone()),
            _ => {}
        }
    }
    Outcome::Failed("run ended without a terminal event".to_owned())
}

/// Writes tool-call lifecycle lines to stderr as they happen. Deliberately
/// minimal: `-p` text mode's contract is "progress on stderr, the answer
/// alone on stdout", not a full transcript.
struct TextProgressObserver;

impl SessionEventObserver for TextProgressObserver {
    fn observe(&self, envelope: &EventEnvelope, _visibility: EventVisibility) {
        match &envelope.event {
            Event::ToolCallRequested {
                name, arguments, ..
            } => eprintln!("\u{2192} {name} {arguments}"),
            Event::ToolCallCompleted { name, is_error, .. } if *is_error => {
                eprintln!("\u{2717} {name} failed")
            }
            _ => {}
        }
    }
}

/// Streams every client-visible Event to stdout as one JSON line, live.
struct JsonlObserver;

impl SessionEventObserver for JsonlObserver {
    fn observe(&self, envelope: &EventEnvelope, _visibility: EventVisibility) {
        match jsonl_line(envelope) {
            Some(Ok(line)) => println!("{line}"),
            Some(Err(error)) => eprintln!("structure -p: failed to serialize an event: {error}"),
            None => {}
        }
    }
}

/// The decision `JsonlObserver` acts on, pulled out so it is testable
/// without capturing stdout: `None` for an Event this stream must never
/// carry (`Event::is_client_visible` is false for it -- system prompts,
/// raw provider reasoning), `Some` otherwise.
fn jsonl_line(envelope: &EventEnvelope) -> Option<Result<String, serde_json::Error>> {
    if !envelope.event.is_client_visible() {
        return None;
    }
    Some(serde_json::to_string(envelope))
}

/// Runs one task to completion and returns the process exit code. Never
/// panics on an ordinary failure (provider error, run failure, cancellation)
/// -- those are reported through the return code and, in text mode, stderr
/// -- but does call `std::process::exit(130)` directly from the Ctrl-C
/// listener on a *second* interrupt, since there is no value to return from
/// mid-run at that point.
pub async fn run(provider_config: ApiProviderConfig, options: PrintOptions) -> i32 {
    let policy = match tool_policy(options.allow_shell, options.read_only) {
        Ok(policy) => policy,
        Err(message) => {
            eprintln!("error: {message}");
            return EXIT_CONFIG_ERROR;
        }
    };
    let model = match ApiModelProvider::new(provider_config) {
        Ok(model) => HostModel::Api(model),
        Err(error) => {
            eprintln!("error: {error}");
            return EXIT_CONFIG_ERROR;
        }
    };
    let runner_root = match std::env::current_dir() {
        Ok(root) => root,
        Err(error) => {
            eprintln!("error: {error}");
            return EXIT_CONFIG_ERROR;
        }
    };
    let arabica_home = match arabica_adapters::default_arabica_home() {
        Ok(home) => home,
        Err(error) => {
            eprintln!("error: {error}");
            return EXIT_CONFIG_ERROR;
        }
    };
    let workspace_id = workspace_id_for(&runner_root);
    // MCP servers come from the user config file; a broken one is reported
    // and skipped rather than failing the task, but a malformed config is
    // a configuration error.
    let mcp = match crate::mcp::connect_configured(&runner_root, &arabica_home).await {
        Ok((mcp, diagnostics)) => {
            for diagnostic in diagnostics {
                eprintln!("structure: {diagnostic}");
            }
            mcp
        }
        Err(message) => {
            eprintln!("error: {message}");
            return EXIT_CONFIG_ERROR;
        }
    };

    // Finding what to resume is a configuration question -- "does the
    // session the user named exist" -- not a run failure, so it is answered
    // here, before there is a runtime to fail, and reported as such
    // (EXIT_CONFIG_ERROR) rather than folded into run_task's own failure
    // handling (EXIT_FAILED, for a provider or runtime problem once a run
    // is actually under way).
    let resumed = match resolve_resume(&options.resume, &arabica_home, &workspace_id) {
        Ok(resumed) => resumed,
        Err(message) => {
            eprintln!("error: {message}");
            return EXIT_CONFIG_ERROR;
        }
    };

    match run_task(
        model,
        &runner_root,
        policy,
        options,
        arabica_home,
        workspace_id,
        resumed,
        mcp,
    )
    .await
    {
        Ok(outcome) => report(outcome),
        Err(error) => {
            eprintln!("error: {error}");
            EXIT_FAILED
        }
    }
}

/// Resolves `--continue`/`--resume <ID>` to the stored session (if any) that
/// `run_task` should append to, without touching the runtime: a pure lookup
/// against `$ARABICA_HOME` so the "does this session exist" question is
/// directly testable without spinning up a model or a `SessionManager`.
pub(crate) fn resolve_resume(
    resume: &Resume,
    arabica_home: &Path,
    workspace_id: &WorkspaceId,
) -> Result<Option<StoredSession>, String> {
    match resume {
        Resume::None => Ok(None),
        Resume::Continue => {
            let listings = FileSessionStore::list_sessions(arabica_home, Some(workspace_id))
                .map_err(|error| error.to_string())?;
            let Some(most_recent) = listings.first() else {
                return Err("no session to continue in this workspace".to_owned());
            };
            FileSessionStore::read(&most_recent.path)
                .map(Some)
                .map_err(|error| error.to_string())
        }
        Resume::Id(id) => FileSessionStore::read_session(
            arabica_home,
            workspace_id,
            &arabica_protocol::SessionId::new(id.clone()),
        )
        .map(Some)
        .map_err(|error| format!("could not resume session {id}: {error}")),
    }
}

#[allow(clippy::too_many_arguments)]
async fn run_task(
    model: HostModel,
    runner_root: &Path,
    policy: LocalRunnerPolicy,
    options: PrintOptions,
    arabica_home: PathBuf,
    workspace_id: WorkspaceId,
    resumed: Option<StoredSession>,
    mcp: crate::mcp::McpTools,
) -> Result<Outcome, Box<dyn std::error::Error>> {
    let runtime: HostRuntime =
        build_host_runtime_with_mcp(model, runner_root, policy, &arabica_home, mcp);
    let mut manager = SessionManager::with_ids(runtime, Box::new(crate::host::UuidIds));
    let instructions_sha256 = crate::instructions::sha256(manager.runtime().system_instructions());

    let (session_id, store) = match resumed {
        Some(stored) => {
            let session_id = stored.header.id.clone();
            let path = FileSessionStore::session_path(&arabica_home, &workspace_id, &session_id);
            manager.restore_session(
                stored.into_snapshot(),
                CommandId::new(uuid::Uuid::now_v7().to_string()),
                None,
            )?;
            let store = FileSessionStore::open_existing(&path)?;
            if manager
                .session(&session_id)
                .is_some_and(|session| session.status == SessionStatus::Suspended)
            {
                let resumed = manager
                    .dispatch(
                        CommandEnvelope::new(
                            CommandId::new(uuid::Uuid::now_v7().to_string()),
                            Some(session_id.clone()),
                            Command::SessionResume,
                        ),
                        DispatchControl::default(),
                    )
                    .await?;
                for event in &resumed {
                    store.observe(event, EventVisibility::Client);
                }
            }
            (session_id, store)
        }
        None => {
            let create = CommandEnvelope::new(
                CommandId::new(uuid::Uuid::now_v7().to_string()),
                None,
                Command::SessionCreate {
                    workspace_id: workspace_id.clone(),
                },
            );
            let created = manager.dispatch(create, DispatchControl::default()).await?;
            let session_created = created
                .into_iter()
                .next()
                .expect("session.create always produces at least one Event");
            let session_id = session_created.session_id.clone();
            let store = FileSessionStore::create(
                &arabica_home,
                NewSession {
                    session_id: &session_id,
                    workspace_id: &workspace_id,
                    cwd: runner_root,
                    profile: None,
                    instructions_sha256: Some(&instructions_sha256),
                },
            )?;
            // dispatch's own return value already has this Event; nothing
            // observed it live because the store could not exist before its
            // own session_id -- allocated inside that same dispatch call --
            // was known. Persisting it here closes that one-event gap
            // rather than leaving session.created permanently missing from
            // the file restore_session later requires it to start with.
            store.observe(&session_created, EventVisibility::Client);
            (session_id, store)
        }
    };

    let progress_observer: Arc<dyn SessionEventObserver> = match options.output_format {
        OutputFormat::Text => Arc::new(TextProgressObserver),
        OutputFormat::Jsonl => Arc::new(JsonlObserver),
    };
    let observer: Arc<dyn SessionEventObserver> = Arc::new(FanOutObserver::new(vec![
        Arc::new(store) as Arc<dyn SessionEventObserver>,
        progress_observer,
    ]));

    let cancellation = RunCancellation::new();
    let ctrl_c = tokio::spawn({
        let cancellation = cancellation.clone();
        async move {
            loop {
                if tokio::signal::ctrl_c().await.is_err() {
                    return;
                }
                if cancellation.is_cancelled() {
                    eprintln!("structure: second interrupt, exiting immediately");
                    std::process::exit(EXIT_CANCELLED);
                }
                eprintln!("structure: interrupted, cancelling the current run...");
                cancellation.cancel();
            }
        }
    });

    let prompt = CommandEnvelope::new(
        CommandId::new(uuid::Uuid::now_v7().to_string()),
        Some(session_id),
        Command::MessageSend {
            content: options.task,
        },
    );
    let control = DispatchControl {
        run: RunControl {
            cancellation: Some(cancellation),
            permissions: None,
        },
        observer: Some(observer),
    };
    let events = manager.dispatch(prompt, control).await?;
    ctrl_c.abort();
    Ok(classify(&events))
}

fn report(outcome: Outcome) -> i32 {
    match outcome {
        Outcome::Completed(output) => {
            if let Some(output) = output {
                println!("{output}");
            }
            EXIT_COMPLETED
        }
        Outcome::Cancelled => {
            eprintln!("structure: cancelled");
            EXIT_CANCELLED
        }
        Outcome::Failed(message) => {
            eprintln!("error: {message}");
            EXIT_FAILED
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_dash_reads_the_task_from_stdin_and_anything_else_passes_through() {
        assert_eq!(resolve_task("fix the bug").unwrap(), "fix the bug");
        // "-" is exercised via the CLI-level read path, not unit-tested
        // here: swapping process stdin out from under a test is not worth
        // the fragility it would add.
    }

    #[test]
    fn allow_shell_and_read_only_together_is_a_configuration_error() {
        assert!(tool_policy(true, true).is_err());
    }

    #[test]
    fn allow_shell_adds_shell_to_the_coding_policy() {
        let policy = tool_policy(true, false).expect("valid combination");
        assert_eq!(
            policy,
            LocalRunnerPolicy::coding().with_tool(LocalTool::Shell)
        );
    }

    #[test]
    fn read_only_never_includes_shell_or_any_mutating_tool() {
        let policy = tool_policy(false, true).expect("valid combination");
        assert_eq!(policy, LocalRunnerPolicy::read_only());
    }

    #[test]
    fn the_default_policy_is_coding_without_shell() {
        let policy = tool_policy(false, false).expect("valid combination");
        assert_eq!(policy, LocalRunnerPolicy::coding());
    }

    fn envelope(event: Event) -> EventEnvelope {
        EventEnvelope {
            protocol_version: arabica_protocol::PROTOCOL_VERSION.to_owned(),
            event_id: arabica_protocol::EventId::new("event-1"),
            command_id: arabica_protocol::CommandId::new("command-1"),
            workspace_id: arabica_protocol::WorkspaceId::new("ws-1"),
            session_id: arabica_protocol::SessionId::new("session-1"),
            run_id: Some(arabica_protocol::RunId::new("run-1")),
            sequence: 1,
            occurred_at_ms: 0,
            event,
        }
    }

    #[test]
    fn classify_prefers_cancelled_over_a_terminal_completion() {
        let events = vec![
            envelope(Event::RunCompleted {
                output: Some("done".to_owned()),
            }),
            envelope(Event::RunCancelled),
        ];
        assert!(matches!(classify(&events), Outcome::Cancelled));
    }

    #[test]
    fn classify_extracts_the_completed_output() {
        let events = vec![envelope(Event::RunCompleted {
            output: Some("the answer".to_owned()),
        })];
        match classify(&events) {
            Outcome::Completed(Some(output)) => assert_eq!(output, "the answer"),
            _ => panic!("expected Completed(Some(..))"),
        }
    }

    #[test]
    fn classify_extracts_the_failure_message() {
        let events = vec![envelope(Event::RunFailed {
            message: "boom".to_owned(),
        })];
        match classify(&events) {
            Outcome::Failed(message) => assert_eq!(message, "boom"),
            _ => panic!("expected Failed"),
        }
    }

    #[test]
    fn report_maps_each_outcome_to_its_documented_exit_code() {
        assert_eq!(report(Outcome::Completed(None)), EXIT_COMPLETED);
        assert_eq!(
            report(Outcome::Completed(Some("hi".to_owned()))),
            EXIT_COMPLETED
        );
        assert_eq!(report(Outcome::Cancelled), EXIT_CANCELLED);
        assert_eq!(report(Outcome::Failed("x".to_owned())), EXIT_FAILED);
    }

    #[test]
    fn jsonl_line_is_none_for_an_internal_event_and_some_for_a_client_visible_one() {
        // model.response.item is internal (Event::is_client_visible is
        // false for it). A leaked provider exchange -- system prompt, raw
        // reasoning -- in a stream built for scripting would be a real
        // information leak, not just noise, so this exercises the actual
        // decision `JsonlObserver::observe` acts on, not a stand-in
        // assertion about `is_client_visible` in isolation.
        let internal = envelope(Event::ModelResponseItem {
            model_step: 1,
            item_index: 0,
            item: arabica_model::RuntimeItem::Reasoning(arabica_model::ReasoningItem {
                id: None,
                summary: vec!["secret reasoning".to_owned()],
                provider_state: None,
            }),
        });
        assert!(jsonl_line(&internal).is_none());

        let client_visible = envelope(Event::RunStarted);
        let line = jsonl_line(&client_visible)
            .expect("a client-visible event must produce a line")
            .expect("serialization succeeds");
        assert!(line.contains("run.started"));
    }

    fn temp_home(label: &str) -> PathBuf {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock is valid")
            .as_nanos();
        std::env::temp_dir().join(format!("arabica-cli-print-{label}-{unique}"))
    }

    fn seed_session(home: &Path, workspace_id: &WorkspaceId) -> arabica_protocol::SessionId {
        let session_id = arabica_protocol::SessionId::new("session-1");
        FileSessionStore::create(
            home,
            NewSession {
                session_id: &session_id,
                workspace_id,
                cwd: Path::new("/repo"),
                profile: None,
                instructions_sha256: None,
            },
        )
        .expect("store creates");
        session_id
    }

    #[test]
    fn resume_none_resolves_to_nothing_without_touching_the_filesystem() {
        // A arabica_home that does not exist: if this ever touched the
        // filesystem, the lookup itself would error rather than returning
        // Ok(None).
        let home = PathBuf::from("/nonexistent/does-not-exist");
        let workspace_id = WorkspaceId::new("ws-1");
        assert!(
            resolve_resume(&Resume::None, &home, &workspace_id)
                .expect("None never fails")
                .is_none()
        );
    }

    #[test]
    fn continue_with_no_sessions_in_the_workspace_is_a_configuration_error() {
        let home = temp_home("continue-empty");
        let workspace_id = WorkspaceId::new("ws-1");
        std::fs::create_dir_all(&home).expect("home creates");

        let error = resolve_resume(&Resume::Continue, &home, &workspace_id)
            .expect_err("no session exists to continue");
        assert!(
            error.contains("no session to continue"),
            "unexpected message: {error}"
        );

        std::fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn continue_resolves_to_the_most_recently_active_session() {
        let home = temp_home("continue-hit");
        let workspace_id = WorkspaceId::new("ws-1");
        let session_id = seed_session(&home, &workspace_id);

        let resumed = resolve_resume(&Resume::Continue, &home, &workspace_id)
            .expect("a session exists to continue")
            .expect("Continue resolves to Some when a session exists");
        assert_eq!(resumed.header.id, session_id);

        std::fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn resume_by_an_unknown_id_is_a_configuration_error() {
        let home = temp_home("resume-unknown");
        let workspace_id = WorkspaceId::new("ws-1");
        std::fs::create_dir_all(&home).expect("home creates");

        let error = resolve_resume(
            &Resume::Id("no-such-session".to_owned()),
            &home,
            &workspace_id,
        )
        .expect_err("the named session does not exist");
        assert!(
            error.contains("could not resume session no-such-session"),
            "unexpected message: {error}"
        );

        std::fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn resume_by_a_known_id_resolves_to_that_session() {
        let home = temp_home("resume-hit");
        let workspace_id = WorkspaceId::new("ws-1");
        let session_id = seed_session(&home, &workspace_id);

        let resumed = resolve_resume(&Resume::Id(session_id.0.clone()), &home, &workspace_id)
            .expect("the named session exists")
            .expect("Id resolves to Some when the session exists");
        assert_eq!(resumed.header.id, session_id);

        std::fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn resume_by_id_does_not_see_a_session_in_a_different_workspace() {
        let home = temp_home("resume-cross-workspace");
        let other_workspace = WorkspaceId::new("ws-other");
        seed_session(&home, &other_workspace);

        let this_workspace = WorkspaceId::new("ws-1");
        let error = resolve_resume(&Resume::Id("session-1".to_owned()), &home, &this_workspace)
            .expect_err("session-1 belongs to a different workspace");
        assert!(
            error.contains("could not resume session session-1"),
            "unexpected message: {error}"
        );

        std::fs::remove_dir_all(&home).ok();
    }
}
