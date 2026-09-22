//! `structure -p`: run one task non-interactively and exit.
//!
//! Unlike `structure acp` (`crates/structure-cli/src/acp/`), there is no
//! client on the other end to ask for permission, so this binding never
//! wires a permission gate at all: what a tool policy admits, it runs
//! outright, and what it excludes is invisible to the model. `--allow-shell`
//! and `--read-only` are the only controls, decided once at startup, not
//! per call. See `docs/runtime_core_architecture.md` Appendix B.

use std::io::Read;
use std::path::Path;

use structure_protocol::{Command, CommandEnvelope, CommandId, Event, EventEnvelope};
use structure_provider::{ApiModelProvider, ApiProviderConfig};
use structure_runner::LocalTool;
use structure_runtime::{RunCancellation, RunControl};
use structure_session::{DispatchControl, EventVisibility, SessionEventObserver, SessionManager};

use crate::host::{
    HostModel, HostRuntime, LocalRunnerPolicy, build_host_runtime, workspace_id_for,
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
    /// `structure_protocol::Event::is_client_visible` already draws, so
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

    match run_task(model, &runner_root, policy, options).await {
        Ok(outcome) => report(outcome),
        Err(session_error) => {
            eprintln!("error: {session_error}");
            EXIT_FAILED
        }
    }
}

async fn run_task(
    model: HostModel,
    runner_root: &Path,
    policy: LocalRunnerPolicy,
    options: PrintOptions,
) -> Result<Outcome, structure_session::SessionError> {
    let runtime: HostRuntime = build_host_runtime(model, runner_root, policy);
    let mut manager = SessionManager::new(runtime);

    let create = CommandEnvelope::new(
        CommandId::new(uuid::Uuid::now_v7().to_string()),
        None,
        Command::SessionCreate {
            workspace_id: workspace_id_for(runner_root),
        },
    );
    let created = manager.dispatch(create, DispatchControl::default()).await?;
    let session_id = created
        .first()
        .map(|envelope| envelope.session_id.clone())
        .expect("session.create always produces at least one Event");

    let observer: std::sync::Arc<dyn SessionEventObserver> = match options.output_format {
        OutputFormat::Text => std::sync::Arc::new(TextProgressObserver),
        OutputFormat::Jsonl => std::sync::Arc::new(JsonlObserver),
    };
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
            protocol_version: structure_protocol::PROTOCOL_VERSION.to_owned(),
            event_id: structure_protocol::EventId::new("event-1"),
            command_id: structure_protocol::CommandId::new("command-1"),
            workspace_id: structure_protocol::WorkspaceId::new("ws-1"),
            session_id: structure_protocol::SessionId::new("session-1"),
            run_id: Some(structure_protocol::RunId::new("run-1")),
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
            item: structure_model::RuntimeItem::Reasoning(structure_model::ReasoningItem {
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
}
