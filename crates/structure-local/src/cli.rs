use crate::text::{
    print_artifact_preview, print_artifacts, print_core_capabilities, print_core_manifest,
    print_events, print_knowledge_preview, print_knowledge_source, print_knowledge_sources,
    print_local_evidence_bundle, print_run_attempt, print_run_evidence_summary, print_run_summary,
    print_runs, print_snapshot, print_surface_parity_report, print_surfaces, print_workspace,
    print_workspace_event_feed, print_workspace_replay, print_workspaces,
};
use crate::tui;
use anyhow::{anyhow, Result};
use clap::{Args, Parser, Subcommand};
use std::io::{self, Write};
use std::path::PathBuf;
use std::sync::mpsc;
use std::thread;
use std::time::Duration;
use structure_local_core::{
    collect_snapshot, default_repo_root, product_surfaces, snapshot_json, structure_core_manifest,
};
use structure_local_runtime::{
    LocalAgentMode, LocalAgentRuntime, LocalToolCall, RunAttempt, RunRequest,
};

pub(crate) const PREVIEW_MAX_BYTES: u64 = 1_000_000;

#[derive(Debug, Parser)]
#[command(name = "structure-local")]
#[command(about = "Rust local CLI/TUI for Structure")]
struct Cli {
    #[arg(long, global = true)]
    repo_root: Option<PathBuf>,
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Status(StatusArgs),
    Surfaces(SurfacesArgs),
    Core(CoreArgs),
    Parity(ParityArgs),
    Chat(ChatArgs),
    Run(RunArgs),
    Runs {
        #[command(subcommand)]
        command: RunsCommand,
    },
    Workspace {
        #[command(subcommand)]
        command: WorkspaceCommand,
    },
    Knowledge {
        #[command(subcommand)]
        command: KnowledgeCommand,
    },
    Artifacts {
        #[command(subcommand)]
        command: ArtifactsCommand,
    },
    Proposals {
        #[command(subcommand)]
        command: ProposalsCommand,
    },
    Evidence {
        #[command(subcommand)]
        command: EvidenceCommand,
    },
    Tui,
}

#[derive(Debug, Args)]
struct StatusArgs {
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct SurfacesArgs {
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct CoreArgs {
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ParityArgs {
    #[arg(long)]
    verify: bool,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ChatArgs {
    prompt: Option<String>,
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long)]
    json: bool,
    #[arg(long)]
    chat_only: bool,
}

#[derive(Debug)]
struct ChatSessionState {
    workspace_id: Option<String>,
    mode: LocalAgentMode,
    last_run_id: Option<String>,
}

#[derive(Debug, Args)]
struct RunArgs {
    prompt: String,
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Subcommand)]
enum RunsCommand {
    List(ListRunsArgs),
    Show(ShowRunArgs),
    Events(RunEventsArgs),
    Evidence(RunEvidenceArgs),
}

#[derive(Debug, Subcommand)]
enum WorkspaceCommand {
    Create(CreateWorkspaceArgs),
    List(ListWorkspacesArgs),
    Show(ShowWorkspaceArgs),
    Replay(WorkspaceReplayArgs),
    Events(WorkspaceEventsArgs),
}

#[derive(Debug, Args)]
struct ListRunsArgs {
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long, default_value_t = 10)]
    limit: usize,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ShowRunArgs {
    run_id: String,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct RunEventsArgs {
    run_id: String,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct RunEvidenceArgs {
    run_id: String,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct CreateWorkspaceArgs {
    workspace_id: String,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ListWorkspacesArgs {
    #[arg(long, default_value_t = 20)]
    limit: usize,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ShowWorkspaceArgs {
    workspace_id: String,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct WorkspaceReplayArgs {
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long, default_value_t = 50)]
    limit: usize,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct WorkspaceEventsArgs {
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long, default_value_t = 0)]
    after: i64,
    #[arg(long, default_value_t = 50)]
    limit: usize,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Subcommand)]
enum KnowledgeCommand {
    Add(AddKnowledgeArgs),
    List(ListKnowledgeArgs),
    Show(ShowKnowledgeArgs),
    Remove(RemoveKnowledgeArgs),
}

#[derive(Debug, Args)]
struct AddKnowledgeArgs {
    path: PathBuf,
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ListKnowledgeArgs {
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long, default_value_t = 20)]
    limit: usize,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ShowKnowledgeArgs {
    source_id: String,
    #[arg(long, default_value_t = PREVIEW_MAX_BYTES)]
    max_bytes: u64,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct RemoveKnowledgeArgs {
    source_id: String,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Subcommand)]
enum ArtifactsCommand {
    List(ListArtifactsArgs),
    Show(ShowArtifactArgs),
}

#[derive(Debug, Subcommand)]
enum ProposalsCommand {
    List(ListProposalsArgs),
    Latest(LatestProposalArgs),
    Show(ShowProposalArgs),
    Apply(ApplyProposalArgs),
}

#[derive(Debug, Subcommand)]
enum EvidenceCommand {
    Bundle(EvidenceBundleArgs),
}

#[derive(Debug, Args)]
struct ListArtifactsArgs {
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long)]
    run: Option<String>,
    #[arg(long, default_value_t = 20)]
    limit: usize,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ShowArtifactArgs {
    artifact_id: String,
    #[arg(long, default_value_t = PREVIEW_MAX_BYTES)]
    max_bytes: u64,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ListProposalsArgs {
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long)]
    run: Option<String>,
    #[arg(long, default_value_t = 20)]
    limit: usize,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct LatestProposalArgs {
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long)]
    run: Option<String>,
    #[arg(long, default_value_t = PREVIEW_MAX_BYTES)]
    max_bytes: u64,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ShowProposalArgs {
    artifact_id: String,
    #[arg(long, default_value_t = PREVIEW_MAX_BYTES)]
    max_bytes: u64,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ApplyProposalArgs {
    artifact_id: String,
    #[arg(long)]
    dry_run: bool,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct EvidenceBundleArgs {
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long, default_value_t = 50)]
    limit: usize,
    #[arg(long)]
    json: bool,
}

pub(crate) fn run() -> Result<()> {
    let cli = Cli::parse();
    let repo_root = match cli.repo_root {
        Some(path) => local_result(structure_local_core::find_repo_root(path))?,
        None => local_result(default_repo_root())?,
    };

    match cli.command {
        Command::Status(args) => run_status(&repo_root, args)?,
        Command::Surfaces(args) => run_surfaces(args)?,
        Command::Core(args) => run_core(args)?,
        Command::Parity(args) => run_parity(&repo_root, args)?,
        Command::Chat(args) => run_chat_agent(&repo_root, args)?,
        Command::Run(args) => run_local_agent(&repo_root, args)?,
        Command::Runs { command } => run_runs(&repo_root, command)?,
        Command::Workspace { command } => run_workspace(&repo_root, command)?,
        Command::Knowledge { command } => run_knowledge(&repo_root, command)?,
        Command::Artifacts { command } => run_artifacts(&repo_root, command)?,
        Command::Proposals { command } => run_proposals(&repo_root, command)?,
        Command::Evidence { command } => run_evidence(&repo_root, command)?,
        Command::Tui => tui::run_tui(&repo_root)?,
    }
    Ok(())
}

fn run_status(repo_root: &PathBuf, args: StatusArgs) -> Result<()> {
    let snapshot = local_result(collect_snapshot(repo_root))?;
    if args.json {
        println!("{}", local_result(snapshot_json(&snapshot))?);
    } else {
        print_snapshot(&snapshot);
    }
    Ok(())
}

fn run_surfaces(args: SurfacesArgs) -> Result<()> {
    let surfaces = product_surfaces();
    if args.json {
        println!("{}", serde_json::to_string_pretty(&surfaces)?);
    } else {
        print_surfaces(&surfaces);
    }
    Ok(())
}

fn run_core(args: CoreArgs) -> Result<()> {
    let manifest = local_result(structure_core_manifest())?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&manifest)?);
    } else {
        print_core_manifest(&manifest);
    }
    Ok(())
}

fn run_parity(repo_root: &PathBuf, args: ParityArgs) -> Result<()> {
    if args.verify {
        let report = local_result(structure_local_core::verify_structure_core_parity_for_repo(
            repo_root,
        ))?;
        if args.json {
            println!("{}", serde_json::to_string_pretty(&report)?);
        } else {
            print_surface_parity_report(&report);
        }
    } else {
        let capabilities = local_result(structure_core_manifest())?.capabilities;
        if args.json {
            println!("{}", serde_json::to_string_pretty(&capabilities)?);
        } else {
            print_core_capabilities(&capabilities);
        }
    }
    Ok(())
}

fn run_local_agent(repo_root: &PathBuf, args: RunArgs) -> Result<()> {
    let request = RunRequest {
        prompt: args.prompt,
        workspace_id: args.workspace,
        mode: Some(LocalAgentMode::CodeAgent),
    };
    let attempt = if args.json {
        let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
        local_result(runtime.run_prompt_attempt(request))?
    } else {
        run_prompt_with_live_events(repo_root, request)?
    };
    if args.json {
        println!("{}", serde_json::to_string_pretty(&attempt)?);
    } else {
        print_run_attempt(&attempt);
    }
    attempt_error(&attempt)
}

fn run_chat_agent(repo_root: &PathBuf, args: ChatArgs) -> Result<()> {
    let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    let mode = if args.chat_only {
        LocalAgentMode::Chat
    } else {
        LocalAgentMode::CodeAgent
    };
    let mut state = ChatSessionState {
        workspace_id: args.workspace.clone(),
        mode,
        last_run_id: None,
    };
    if let Some(prompt) = args.prompt {
        let request = RunRequest {
            prompt,
            workspace_id: state.workspace_id,
            mode: Some(state.mode),
        };
        let attempt = if args.json {
            local_result(runtime.run_prompt_attempt(request))?
        } else {
            run_prompt_with_live_events(repo_root, request)?
        };
        if args.json {
            println!("{}", serde_json::to_string_pretty(&attempt)?);
        } else {
            print_chat_attempt(&attempt);
        }
        return attempt_error(&attempt);
    }

    println!("Structure local chat");
    println!(
        "  workspace: {}",
        state.workspace_id.as_deref().unwrap_or("default")
    );
    println!("  mode:      {}", session_mode_label(&state.mode));
    println!(
        "  commands:  /help, /mode, /workspace, /ls, /search, /read, /source, /runs, /proposal, /apply, /quit"
    );
    println!();

    let stdin = io::stdin();
    loop {
        print!(
            "structure {}:{}> ",
            session_mode_label(&state.mode),
            state.workspace_id.as_deref().unwrap_or("default")
        );
        io::stdout().flush()?;
        let mut prompt = String::new();
        let bytes = stdin.read_line(&mut prompt)?;
        if bytes == 0 {
            break;
        }
        let prompt = prompt.trim();
        if prompt.is_empty() {
            continue;
        }
        if prompt.starts_with('/') {
            if !handle_chat_session_command(&runtime, &mut state, prompt)? {
                break;
            }
            continue;
        }
        let request = RunRequest {
            prompt: prompt.to_string(),
            workspace_id: state.workspace_id.clone(),
            mode: Some(state.mode.clone()),
        };
        let attempt = if args.json {
            local_result(runtime.run_prompt_attempt(request))?
        } else {
            run_prompt_with_live_events(repo_root, request)?
        };
        state.last_run_id = Some(attempt.run.run_id.clone());
        if args.json {
            println!("{}", serde_json::to_string_pretty(&attempt)?);
        } else {
            print_chat_attempt(&attempt);
        }
    }
    Ok(())
}

fn run_prompt_with_live_events(repo_root: &PathBuf, request: RunRequest) -> Result<RunAttempt> {
    let workspace_id = request
        .workspace_id
        .clone()
        .unwrap_or_else(|| "default".to_string());
    let feed_runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    let replay = local_result(feed_runtime.workspace_replay(Some(&workspace_id), 1))?;
    let mut cursor = replay.last_sequence.unwrap_or_default();
    let repo_root = repo_root.clone();
    let (sender, receiver) = mpsc::channel();

    thread::spawn(move || {
        let result = LocalAgentRuntime::open(&repo_root)
            .and_then(|runtime| runtime.run_prompt_attempt(request));
        let _ = sender.send(result);
    });

    println!("Live event feed");
    let run_result = loop {
        match receiver.try_recv() {
            Ok(result) => break result,
            Err(mpsc::TryRecvError::Empty) => {
                cursor = print_live_workspace_events(&feed_runtime, &workspace_id, cursor)?;
                thread::sleep(Duration::from_millis(200));
            }
            Err(mpsc::TryRecvError::Disconnected) => {
                return Err(anyhow!("local run worker disconnected"));
            }
        }
    };
    let _ = print_live_workspace_events(&feed_runtime, &workspace_id, cursor)?;
    println!();
    local_result(run_result)
}

fn attempt_error(attempt: &RunAttempt) -> Result<()> {
    if let Some(error) = &attempt.error {
        return Err(anyhow!(error.clone()));
    }
    Ok(())
}

fn print_live_workspace_events(
    runtime: &LocalAgentRuntime,
    workspace_id: &str,
    after_sequence: i64,
) -> Result<i64> {
    let feed =
        local_result(runtime.workspace_event_feed(Some(workspace_id), Some(after_sequence), 50))?;
    for event in &feed.events {
        println!(
            "  #{:<4} {:<26} {}",
            event.sequence,
            event.kind,
            event.run_id.as_deref().unwrap_or("workspace")
        );
    }
    Ok(feed.next_after_sequence)
}

fn handle_chat_session_command(
    runtime: &LocalAgentRuntime,
    state: &mut ChatSessionState,
    input: &str,
) -> Result<bool> {
    let mut parts = input.split_whitespace();
    let command = parts.next().unwrap_or_default();
    match command {
        "/quit" | "/exit" => Ok(false),
        "/help" => {
            print_chat_session_help();
            Ok(true)
        }
        "/mode" => {
            match parts.next() {
                Some("chat") => state.mode = LocalAgentMode::Chat,
                Some("code") | Some("code_agent") => state.mode = LocalAgentMode::CodeAgent,
                Some(other) => {
                    println!("Unknown mode: {other}");
                    println!("Use /mode chat or /mode code");
                    return Ok(true);
                }
                None => {}
            }
            println!("Mode: {}", session_mode_label(&state.mode));
            Ok(true)
        }
        "/workspace" | "/ws" => {
            if let Some(workspace_id) = parts.next() {
                let workspace =
                    local_result(runtime.ensure_workspace(Some(workspace_id.to_string())))?;
                state.workspace_id = Some(workspace.workspace_id.clone());
                println!("Workspace: {}", workspace.workspace_id);
            } else {
                println!(
                    "Workspace: {}",
                    state.workspace_id.as_deref().unwrap_or("default")
                );
            }
            Ok(true)
        }
        "/source" | "/knowledge" => {
            let Some(path) = parts.next() else {
                println!("Usage: /source <path>");
                return Ok(true);
            };
            let source =
                local_result(runtime.add_knowledge_source(state.workspace_id.clone(), path))?;
            println!("Added knowledge source");
            println!("  id:   {}", source.source_id);
            println!("  file: {}", source.path);
            Ok(true)
        }
        "/sources" => {
            let sources =
                local_result(runtime.knowledge_sources(state.workspace_id.as_deref(), 8))?;
            print_knowledge_sources(&sources);
            Ok(true)
        }
        "/ls" => {
            let result = execute_session_tool(
                runtime,
                state.workspace_id.clone(),
                "list_workspace",
                serde_json::json!({ "max_entries": 32 }),
            );
            print_tool_result(result)?;
            Ok(true)
        }
        "/search" => {
            let query = parts.collect::<Vec<_>>().join(" ");
            if query.trim().is_empty() {
                println!("Usage: /search <query>");
                return Ok(true);
            }
            let result = execute_session_tool(
                runtime,
                state.workspace_id.clone(),
                "search_repo",
                serde_json::json!({
                    "query": query,
                    "max_matches": 20,
                }),
            );
            print_tool_result(result)?;
            Ok(true)
        }
        "/read" | "/open" => {
            let Some(path) = parts.next() else {
                println!("Usage: /read <repo-relative-path>");
                return Ok(true);
            };
            let result = execute_session_tool(
                runtime,
                state.workspace_id.clone(),
                "read_repo_file",
                serde_json::json!({
                    "path": path,
                    "max_bytes": PREVIEW_MAX_BYTES,
                }),
            );
            print_tool_result(result)?;
            Ok(true)
        }
        "/cmd" => {
            let argv = parts.map(str::to_string).collect::<Vec<_>>();
            if argv.is_empty() {
                println!("Usage: /cmd <allowlisted-command> [args...]");
                return Ok(true);
            }
            let result = execute_session_tool(
                runtime,
                state.workspace_id.clone(),
                "run_local_command",
                serde_json::json!({
                    "argv": argv,
                    "cwd": ".",
                    "timeout_ms": 30_000,
                    "max_output_chars": 12_000,
                }),
            );
            print_tool_result(result)?;
            Ok(true)
        }
        "/runs" => {
            let runs = local_result(runtime.list_runs(state.workspace_id.as_deref(), 8))?;
            print_runs(&runs);
            Ok(true)
        }
        "/events" => {
            let run_id = parts
                .next()
                .map(str::to_string)
                .or_else(|| state.last_run_id.clone());
            let Some(run_id) = run_id else {
                println!("No run selected. Run a prompt or use /events <run_id>.");
                return Ok(true);
            };
            let events = local_result(runtime.run_events(&run_id))?;
            print_events(&events);
            Ok(true)
        }
        "/evidence" => {
            let run_id = parts
                .next()
                .map(str::to_string)
                .or_else(|| state.last_run_id.clone());
            let Some(run_id) = run_id else {
                println!("No run selected. Run a prompt or use /evidence <run_id>.");
                return Ok(true);
            };
            let evidence = local_result(runtime.run_evidence_summary(&run_id))?;
            print_run_evidence_summary(&evidence);
            Ok(true)
        }
        "/artifacts" => {
            let artifacts = local_result(runtime.list_artifacts(
                state.workspace_id.as_deref(),
                state.last_run_id.as_deref(),
                8,
            ))?;
            print_artifacts(&artifacts);
            Ok(true)
        }
        "/proposal" | "/diff" => {
            let run_id = parts
                .next()
                .map(str::to_string)
                .or_else(|| state.last_run_id.clone());
            match latest_proposal_preview(
                runtime,
                state.workspace_id.as_deref(),
                run_id.as_deref(),
                PREVIEW_MAX_BYTES,
            ) {
                Ok(preview) => print_artifact_preview(&preview),
                Err(error) => println!("{error}"),
            }
            Ok(true)
        }
        "/apply" => {
            let args = parts.collect::<Vec<_>>();
            let dry_run = args.contains(&"--dry-run");
            let selected = args
                .iter()
                .find(|part| **part != "--dry-run")
                .map(|part| (*part).to_string());
            let (artifact_id, run_id) = match selected {
                Some(value) if value.starts_with("art_") => (value, None),
                Some(value) => {
                    let preview = local_result(latest_proposal_preview(
                        runtime,
                        state.workspace_id.as_deref(),
                        Some(&value),
                        PREVIEW_MAX_BYTES,
                    ))?;
                    (preview.artifact.artifact_id, Some(value))
                }
                None => {
                    let preview = local_result(latest_proposal_preview(
                        runtime,
                        state.workspace_id.as_deref(),
                        state.last_run_id.as_deref(),
                        PREVIEW_MAX_BYTES,
                    ))?;
                    (preview.artifact.artifact_id, state.last_run_id.clone())
                }
            };
            let result = local_result(runtime.apply_code_change_proposal(&artifact_id, dry_run))?;
            println!(
                "{} proposal {}",
                if result.applied {
                    "Applied"
                } else {
                    "Previewed"
                },
                result.artifact.artifact_id
            );
            if let Some(run_id) = run_id {
                println!("  run:         {run_id}");
            }
            println!("  target:      {}", result.target_path);
            println!("  added lines: {}", result.added_lines);
            println!("  bytes:       {}", result.bytes_written);
            println!("  dry run:     {}", result.dry_run);
            Ok(true)
        }
        "/replay" => {
            let replay = local_result(runtime.workspace_replay(state.workspace_id.as_deref(), 40))?;
            print_workspace_replay(&replay);
            Ok(true)
        }
        other => {
            println!("Unknown command: {other}");
            println!("Use /help to list session commands.");
            Ok(true)
        }
    }
}

fn session_mode_label(mode: &LocalAgentMode) -> &'static str {
    match mode {
        LocalAgentMode::Chat => "chat",
        LocalAgentMode::CodeAgent => "code_agent",
    }
}

fn print_chat_session_help() {
    println!("Structure local session commands");
    println!("  /mode chat|code       Switch between chat and code-agent mode");
    println!("  /workspace [id]       Show or open/create a workspace");
    println!("  /ls                   List top-level workspace entries");
    println!("  /search <query>       Search repo text through local tools");
    println!("  /read <path>          Read a repo-relative file safely");
    println!("  /cmd <argv...>        Run an allowlisted local check command");
    println!("  /source <path>        Register a knowledge file for this workspace");
    println!("  /sources              List workspace knowledge sources");
    println!("  /runs                 List recent runs in this workspace");
    println!("  /events [run_id]      Show event stream for a run");
    println!("  /evidence [run_id]    Show run evidence summary");
    println!("  /artifacts            List recent artifacts");
    println!("  /proposal [run_id]    Show the latest code-change proposal");
    println!("  /apply [id|run]       Apply a reviewed proposal; add --dry-run to preview");
    println!("  /replay               Replay workspace event stream");
    println!("  /quit                 Exit");
}

fn execute_session_tool(
    runtime: &LocalAgentRuntime,
    workspace_id: Option<String>,
    name: &str,
    input: serde_json::Value,
) -> Result<structure_local_runtime::LocalToolResult, String> {
    runtime.execute_workspace_tool(
        workspace_id,
        "cli_session",
        LocalToolCall {
            call_id: format!("session_{name}"),
            name: name.to_string(),
            input,
        },
    )
}

fn print_tool_result(
    result: Result<structure_local_runtime::LocalToolResult, String>,
) -> Result<()> {
    let result = local_result(result)?;
    if !result.success {
        println!(
            "{} failed: {}",
            result.name,
            result.error.as_deref().unwrap_or("unknown error")
        );
        return Ok(());
    }

    match result.name.as_str() {
        "search_repo" => {
            let query = result
                .output
                .get("query")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("");
            let matches = result
                .output
                .get("matches")
                .and_then(serde_json::Value::as_array)
                .cloned()
                .unwrap_or_default();
            println!("Search: {query}");
            if matches.is_empty() {
                println!("No matches.");
            }
            for item in matches {
                let path = item
                    .get("path")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("<unknown>");
                let line = item
                    .get("line")
                    .and_then(serde_json::Value::as_u64)
                    .unwrap_or_default();
                let text = item
                    .get("text")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("");
                println!("  {path}:{line}: {text}");
            }
        }
        "read_repo_file" | "read_knowledge_source" => {
            let path = result
                .output
                .get("path")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("<unknown>");
            let chars = result
                .output
                .get("chars_returned")
                .and_then(serde_json::Value::as_u64)
                .unwrap_or_default();
            let truncated = result
                .output
                .get("truncated")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false);
            let preview = result
                .output
                .get("preview")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("");
            println!("{path} ({chars} chars, truncated: {truncated})");
            println!("{preview}");
        }
        "run_local_command" => {
            let argv = result
                .output
                .get("argv")
                .and_then(serde_json::Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .filter_map(serde_json::Value::as_str)
                        .collect::<Vec<_>>()
                        .join(" ")
                })
                .unwrap_or_else(|| "<unknown command>".to_string());
            let exit_code = result
                .output
                .get("exit_code")
                .and_then(serde_json::Value::as_i64)
                .map(|value| value.to_string())
                .unwrap_or_else(|| "signal".to_string());
            let timed_out = result
                .output
                .get("timed_out")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false);
            println!("$ {argv}");
            println!("exit: {exit_code}, timed out: {timed_out}");
            let stdout = result
                .output
                .get("stdout")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("");
            let stderr = result
                .output
                .get("stderr")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("");
            if !stdout.is_empty() {
                println!("\nstdout:\n{stdout}");
            }
            if !stderr.is_empty() {
                println!("\nstderr:\n{stderr}");
            }
        }
        _ => {
            println!("{}", serde_json::to_string_pretty(&result.output)?);
        }
    }
    Ok(())
}

fn print_chat_attempt(attempt: &RunAttempt) {
    let Some(result) = &attempt.result else {
        println!(
            "assistant [{} / {}]",
            attempt.run.run_id, attempt.run.status
        );
        println!(
            "{}",
            attempt
                .error
                .as_deref()
                .unwrap_or("local run failed without an error message")
        );
        println!(
            "\n[event-sourced: {} events, failed run is inspectable with `runs events {}`]",
            attempt.events.len(),
            attempt.run.run_id
        );
        return;
    };
    println!("assistant [{} / {}]", result.run.run_id, result.run.status);
    println!("{}", result.final_response);
    println!(
        "\n[event-sourced: {} events, artifact: {}]",
        result.events.len(),
        result.artifact_path
    );
}

fn run_runs(repo_root: &PathBuf, command: RunsCommand) -> Result<()> {
    let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    match command {
        RunsCommand::List(args) => {
            let runs = local_result(runtime.list_runs(args.workspace.as_deref(), args.limit))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&runs)?);
            } else {
                print_runs(&runs);
            }
        }
        RunsCommand::Show(args) => {
            let run = local_result(runtime.run_by_id(&args.run_id))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&run)?);
            } else {
                print_run_summary(&run);
            }
        }
        RunsCommand::Events(args) => {
            let events = local_result(runtime.run_events(&args.run_id))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&events)?);
            } else {
                print_events(&events);
            }
        }
        RunsCommand::Evidence(args) => {
            let evidence = local_result(runtime.run_evidence_summary(&args.run_id))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&evidence)?);
            } else {
                print_run_evidence_summary(&evidence);
            }
        }
    }
    Ok(())
}

fn run_workspace(repo_root: &PathBuf, command: WorkspaceCommand) -> Result<()> {
    let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    match command {
        WorkspaceCommand::Create(args) => {
            let workspace = local_result(runtime.ensure_workspace(Some(args.workspace_id)))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&workspace)?);
            } else {
                print_workspace(&workspace);
            }
        }
        WorkspaceCommand::List(args) => {
            let workspaces = local_result(runtime.list_workspaces(args.limit))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&workspaces)?);
            } else {
                print_workspaces(&workspaces);
            }
        }
        WorkspaceCommand::Show(args) => {
            let workspace = local_result(runtime.workspace(&args.workspace_id))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&workspace)?);
            } else {
                print_workspace(&workspace);
            }
        }
        WorkspaceCommand::Replay(args) => {
            let replay =
                local_result(runtime.workspace_replay(args.workspace.as_deref(), args.limit))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&replay)?);
            } else {
                print_workspace_replay(&replay);
            }
        }
        WorkspaceCommand::Events(args) => {
            let feed = local_result(runtime.workspace_event_feed(
                args.workspace.as_deref(),
                Some(args.after),
                args.limit,
            ))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&feed)?);
            } else {
                print_workspace_event_feed(&feed);
            }
        }
    }
    Ok(())
}

fn run_knowledge(repo_root: &PathBuf, command: KnowledgeCommand) -> Result<()> {
    let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    match command {
        KnowledgeCommand::Add(args) => {
            let source = local_result(runtime.add_knowledge_source(args.workspace, args.path))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&source)?);
            } else {
                println!("Added knowledge source");
                println!("  id:   {}", source.source_id);
                println!("  file: {}", source.path);
            }
        }
        KnowledgeCommand::List(args) => {
            let sources =
                local_result(runtime.knowledge_sources(args.workspace.as_deref(), args.limit))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&sources)?);
            } else {
                print_knowledge_sources(&sources);
            }
        }
        KnowledgeCommand::Show(args) => {
            let preview =
                local_result(runtime.read_knowledge_source(&args.source_id, args.max_bytes))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&preview)?);
            } else {
                print_knowledge_preview(&preview);
            }
        }
        KnowledgeCommand::Remove(args) => {
            let source = local_result(runtime.remove_knowledge_source(&args.source_id))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&source)?);
            } else {
                println!("Removed knowledge source registration");
                print_knowledge_source(&source);
            }
        }
    }
    Ok(())
}

fn run_artifacts(repo_root: &PathBuf, command: ArtifactsCommand) -> Result<()> {
    let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    match command {
        ArtifactsCommand::List(args) => {
            let artifacts = local_result(runtime.list_artifacts(
                args.workspace.as_deref(),
                args.run.as_deref(),
                args.limit,
            ))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&artifacts)?);
            } else {
                print_artifacts(&artifacts);
            }
        }
        ArtifactsCommand::Show(args) => {
            let preview = local_result(runtime.read_artifact(&args.artifact_id, args.max_bytes))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&preview)?);
            } else {
                print_artifact_preview(&preview);
            }
        }
    }
    Ok(())
}

fn run_proposals(repo_root: &PathBuf, command: ProposalsCommand) -> Result<()> {
    let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    match command {
        ProposalsCommand::List(args) => {
            let proposals = local_result(runtime.list_artifacts(
                args.workspace.as_deref(),
                args.run.as_deref(),
                args.limit,
            ))?
            .into_iter()
            .filter(|artifact| artifact.kind == "code_change_proposal")
            .collect::<Vec<_>>();
            if args.json {
                println!("{}", serde_json::to_string_pretty(&proposals)?);
            } else {
                print_artifacts(&proposals);
            }
        }
        ProposalsCommand::Latest(args) => {
            let preview = local_result(latest_proposal_preview(
                &runtime,
                args.workspace.as_deref(),
                args.run.as_deref(),
                args.max_bytes,
            ))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&preview)?);
            } else {
                print_artifact_preview(&preview);
            }
        }
        ProposalsCommand::Show(args) => {
            let preview = local_result(runtime.read_artifact(&args.artifact_id, args.max_bytes))?;
            if preview.artifact.kind != "code_change_proposal" {
                return Err(anyhow!(
                    "artifact {} is {}, not code_change_proposal",
                    preview.artifact.artifact_id,
                    preview.artifact.kind
                ));
            }
            if args.json {
                println!("{}", serde_json::to_string_pretty(&preview)?);
            } else {
                print_artifact_preview(&preview);
            }
        }
        ProposalsCommand::Apply(args) => {
            let result =
                local_result(runtime.apply_code_change_proposal(&args.artifact_id, args.dry_run))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                println!(
                    "{} code-change proposal {}",
                    if result.applied {
                        "Applied"
                    } else {
                        "Previewed"
                    },
                    result.artifact.artifact_id
                );
                println!("  target:      {}", result.target_path);
                println!("  added lines: {}", result.added_lines);
                println!("  bytes:       {}", result.bytes_written);
                println!("  dry run:     {}", result.dry_run);
                if result.dry_run {
                    println!();
                    println!("{}", result.preview);
                }
            }
        }
    }
    Ok(())
}

fn latest_proposal_preview(
    runtime: &LocalAgentRuntime,
    workspace_id: Option<&str>,
    run_id: Option<&str>,
    max_bytes: u64,
) -> std::result::Result<structure_local_runtime::ArtifactPreview, String> {
    let proposal = runtime
        .list_artifacts(workspace_id, run_id, 32)?
        .into_iter()
        .find(|artifact| artifact.kind == "code_change_proposal")
        .ok_or_else(|| "No local code-change proposal found.".to_string())?;
    runtime.read_artifact(&proposal.artifact_id, max_bytes)
}

fn run_evidence(repo_root: &PathBuf, command: EvidenceCommand) -> Result<()> {
    let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    match command {
        EvidenceCommand::Bundle(args) => {
            let bundle =
                local_result(runtime.local_evidence_bundle(args.workspace.as_deref(), args.limit))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&bundle)?);
            } else {
                print_local_evidence_bundle(&bundle);
            }
        }
    }
    Ok(())
}

fn local_result<T>(result: std::result::Result<T, String>) -> Result<T> {
    result.map_err(|message| anyhow!(message))
}
