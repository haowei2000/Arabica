use crate::text::{
    print_agent_context, print_artifact_preview, print_artifacts, print_core_capabilities,
    print_core_manifest, print_event_gc_preview, print_events, print_knowledge_preview,
    print_knowledge_source, print_knowledge_sources, print_local_evidence_bundle,
    print_model_usage_summary, print_proposal_review, print_run_attempt, print_run_compact,
    print_run_core_trace, print_run_evidence_summary, print_run_plan, print_run_review,
    print_run_summary, print_run_transcript, print_runs, print_snapshot, print_source_rating,
    print_surface_parity_report, print_surfaces, print_tool_trace, print_workspace,
    print_workspace_compact, print_workspace_event_feed, print_workspace_replay, print_workspaces,
    print_worktree_snapshot,
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
    LocalSnapshot,
};
use structure_local_runtime::{
    ContinuationRequest, LocalAgentContext, LocalAgentMode, LocalAgentRuntime, LocalLlmDiagnostic,
    LocalToolCall, ProposalApplyResult, RunAttempt, RunRequest,
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
    Worktree(WorktreeArgs),
    Context(ContextArgs),
    Doctor(DoctorArgs),
    Surfaces(SurfacesArgs),
    Core(CoreArgs),
    Parity(ParityArgs),
    Llm {
        #[command(subcommand)]
        command: LlmCommand,
    },
    Chat(ChatArgs),
    Run(RunArgs),
    Continue(ContinueArgs),
    Retry(RetryArgs),
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
struct WorktreeArgs {
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ContextArgs {
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long, value_name = "chat|code_agent")]
    mode: Option<String>,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct DoctorArgs {
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long, value_name = "chat|code_agent")]
    mode: Option<String>,
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

#[derive(Debug, Subcommand)]
enum LlmCommand {
    Check(LlmCheckArgs),
}

#[derive(Debug, Args)]
struct LlmCheckArgs {
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ChatArgs {
    prompt: Option<String>,
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long, value_name = "chat|code_agent")]
    mode: Option<String>,
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
    #[arg(long, value_name = "chat|code_agent")]
    mode: Option<String>,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ContinueArgs {
    run_id: String,
    #[arg(value_name = "instruction")]
    instruction: Vec<String>,
    #[arg(long, value_name = "chat|code_agent")]
    mode: Option<String>,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct RetryArgs {
    run_id: String,
    #[arg(long, value_name = "chat|code_agent")]
    mode: Option<String>,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Subcommand)]
enum RunsCommand {
    List(ListRunsArgs),
    Show(ShowRunArgs),
    Events(RunEventsArgs),
    Plan(RunPlanArgs),
    Trace(RunTraceArgs),
    Review(RunReviewArgs),
    Compact(RunCompactArgs),
    Evidence(RunEvidenceArgs),
    Transcript(RunTranscriptArgs),
    Inspect(RunTranscriptArgs),
}

#[derive(Debug, Subcommand)]
enum WorkspaceCommand {
    Create(CreateWorkspaceArgs),
    List(ListWorkspacesArgs),
    Show(ShowWorkspaceArgs),
    Replay(WorkspaceReplayArgs),
    Compact(WorkspaceCompactArgs),
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
struct RunPlanArgs {
    run_id: String,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct RunTraceArgs {
    run_id: String,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct RunReviewArgs {
    run_id: String,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct RunCompactArgs {
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
struct RunTranscriptArgs {
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
struct WorkspaceCompactArgs {
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long, default_value_t = 8)]
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
    Review(ReviewProposalArgs),
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
struct ReviewProposalArgs {
    artifact_id: String,
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
        Command::Worktree(args) => run_worktree(&repo_root, args)?,
        Command::Context(args) => run_context(&repo_root, args)?,
        Command::Doctor(args) => run_doctor(&repo_root, args)?,
        Command::Surfaces(args) => run_surfaces(args)?,
        Command::Core(args) => run_core(args)?,
        Command::Parity(args) => run_parity(&repo_root, args)?,
        Command::Llm { command } => run_llm(&repo_root, command)?,
        Command::Chat(args) => run_chat_agent(&repo_root, args)?,
        Command::Run(args) => run_local_agent(&repo_root, args)?,
        Command::Continue(args) => run_continue_agent(&repo_root, args)?,
        Command::Retry(args) => run_retry_agent(&repo_root, args)?,
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

fn run_llm(repo_root: &PathBuf, command: LlmCommand) -> Result<()> {
    match command {
        LlmCommand::Check(args) => {
            let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
            let diagnostic = runtime.llm_diagnostic();
            if args.json {
                println!("{}", serde_json::to_string_pretty(&diagnostic)?);
            } else {
                print!("{}", render_llm_diagnostic(&diagnostic));
            }
            if !diagnostic.ok {
                return Err(anyhow!(diagnostic
                    .error
                    .clone()
                    .unwrap_or_else(|| "LLM diagnostic failed".to_string())));
            }
        }
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

fn run_worktree(repo_root: &PathBuf, args: WorktreeArgs) -> Result<()> {
    let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    let worktree = runtime.worktree_snapshot();
    if args.json {
        println!("{}", serde_json::to_string_pretty(&worktree)?);
    } else {
        print_worktree_snapshot(&worktree);
    }
    Ok(())
}

fn run_context(repo_root: &PathBuf, args: ContextArgs) -> Result<()> {
    let mode = parse_agent_mode(args.mode.as_deref(), false)?;
    let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    let context = local_result(runtime.agent_context(args.workspace.as_deref(), Some(mode)))?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&context)?);
    } else {
        print_agent_context(&context);
    }
    Ok(())
}

fn run_doctor(repo_root: &PathBuf, args: DoctorArgs) -> Result<()> {
    let mode = parse_agent_mode(args.mode.as_deref(), false)?;
    let snapshot = local_result(collect_snapshot(repo_root))?;
    let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    let diagnostic = runtime.llm_diagnostic();
    let parity = local_result(structure_local_core::verify_structure_core_parity_for_repo(
        repo_root,
    ))?;
    let context = local_result(runtime.agent_context(args.workspace.as_deref(), Some(mode)))?;
    if args.json {
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!({
                "snapshot": snapshot,
                "llm_diagnostic": diagnostic,
                "surface_parity": parity,
                "agent_context": context,
            }))?
        );
    } else {
        print!(
            "{}",
            render_local_doctor_report(&diagnostic, &parity, &context)
        );
    }
    if !diagnostic.ok {
        return Err(anyhow!(diagnostic
            .error
            .clone()
            .unwrap_or_else(|| "LLM diagnostic failed".to_string())));
    }
    if !parity.passed {
        return Err(anyhow!("Structure Core surface parity failed"));
    }
    Ok(())
}

fn render_llm_diagnostic(diagnostic: &LocalLlmDiagnostic) -> String {
    let mut text = String::new();
    text.push_str("Structure local LLM diagnostic\n");
    text.push_str(&format!("  provider:   {}\n", diagnostic.provider));
    text.push_str(&format!("  configured: {}\n", diagnostic.configured));
    text.push_str(&format!("  ok:         {}\n", diagnostic.ok));
    text.push_str(&format!(
        "  model:      {}\n",
        diagnostic.model.as_deref().unwrap_or("not set")
    ));
    text.push_str(&format!(
        "  endpoint:   {}\n",
        diagnostic.endpoint.as_deref().unwrap_or("not set")
    ));
    text.push_str(&format!("  elapsed:    {} ms\n", diagnostic.elapsed_ms));
    if let Some(response) = &diagnostic.response_preview {
        text.push_str(&format!("  response:   {}\n", response));
    }
    if let Some(error) = &diagnostic.error {
        text.push_str(&format!("  error:      {}\n", error));
    }
    text
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
    let mode = parse_agent_mode(args.mode.as_deref(), false)?;
    let request = RunRequest {
        prompt: args.prompt,
        workspace_id: args.workspace,
        mode: Some(mode),
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

fn run_continue_agent(repo_root: &PathBuf, args: ContinueArgs) -> Result<()> {
    let mode = parse_agent_mode(args.mode.as_deref(), false)?;
    let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    let transcript = local_result(runtime.run_transcript(&args.run_id))?;
    let instruction = args.instruction.join(" ").trim().to_string();
    let request = ContinuationRequest {
        run_id: transcript.run.run_id.clone(),
        extra_instruction: (!instruction.is_empty()).then_some(instruction),
        mode: Some(mode),
    };
    let workspace_id = transcript.run.workspace_id;
    let attempt = if args.json {
        local_result(runtime.run_continuation_attempt(request))?
    } else {
        run_continuation_with_live_events(repo_root, workspace_id, request)?
    };
    if args.json {
        println!("{}", serde_json::to_string_pretty(&attempt)?);
    } else {
        print_run_attempt(&attempt);
    }
    attempt_error(&attempt)
}

fn run_retry_agent(repo_root: &PathBuf, args: RetryArgs) -> Result<()> {
    let mode = parse_agent_mode(args.mode.as_deref(), false)?;
    let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    let transcript = local_result(runtime.run_transcript(&args.run_id))?;
    let request = ContinuationRequest {
        run_id: transcript.run.run_id.clone(),
        extra_instruction: Some(retry_instruction().to_string()),
        mode: Some(mode),
    };
    let workspace_id = transcript.run.workspace_id;
    let attempt = if args.json {
        local_result(runtime.run_continuation_attempt(request))?
    } else {
        run_continuation_with_live_events(repo_root, workspace_id, request)?
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
    let mode = parse_agent_mode(args.mode.as_deref(), args.chat_only)?;
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
    println!("  commands:  {}", chat_session_command_summary());
    println!("  context:   mention @repo/relative/path[:line] in a prompt to attach a file");
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
            if !handle_chat_session_command(&runtime, repo_root, args.json, &mut state, prompt)? {
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

fn run_continuation_with_live_events(
    repo_root: &PathBuf,
    workspace_id: String,
    request: ContinuationRequest,
) -> Result<RunAttempt> {
    let feed_runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    let replay = local_result(feed_runtime.workspace_replay(Some(&workspace_id), 1))?;
    let mut cursor = replay.last_sequence.unwrap_or_default();
    let repo_root = repo_root.clone();
    let (sender, receiver) = mpsc::channel();

    thread::spawn(move || {
        let result = LocalAgentRuntime::open(&repo_root)
            .and_then(|runtime| runtime.run_continuation_attempt(request));
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
                return Err(anyhow!("local continuation worker disconnected"));
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
            "  #{:<4} {:<26} {:<10} {}",
            event.sequence,
            event.kind,
            event.canonical_flow_id,
            event.run_id.as_deref().unwrap_or("workspace")
        );
    }
    Ok(feed.next_after_sequence)
}

fn handle_chat_session_command(
    runtime: &LocalAgentRuntime,
    repo_root: &PathBuf,
    json: bool,
    state: &mut ChatSessionState,
    input: &str,
) -> Result<bool> {
    let mut parts = input.split_whitespace();
    let command = parts.next().unwrap_or_default();
    let command_body = input
        .strip_prefix(command)
        .map(str::trim)
        .unwrap_or_default();
    match command {
        "/quit" | "/exit" => Ok(false),
        "/help" => {
            print_chat_session_help();
            Ok(true)
        }
        "/status" => {
            let snapshot = local_result(collect_snapshot(runtime.repo_root()))?;
            print!("{}", render_chat_session_status(state, &snapshot));
            Ok(true)
        }
        "/llm" => {
            let diagnostic = runtime.llm_diagnostic();
            print!("{}", render_llm_diagnostic(&diagnostic));
            Ok(true)
        }
        "/doctor" => {
            let snapshot = local_result(collect_snapshot(runtime.repo_root()))?;
            let diagnostic = runtime.llm_diagnostic();
            let parity = local_result(
                structure_local_core::verify_structure_core_parity_for_repo(runtime.repo_root()),
            )?;
            print!(
                "{}",
                render_chat_session_doctor(state, &snapshot, &diagnostic, &parity)
            );
            Ok(true)
        }
        "/context" | "/ctx" => {
            let context = local_result(
                runtime.agent_context(state.workspace_id.as_deref(), Some(state.mode.clone())),
            )?;
            if json {
                println!("{}", serde_json::to_string_pretty(&context)?);
            } else {
                print_agent_context(&context);
            }
            Ok(true)
        }
        "/worktree" | "/dirty" => {
            let worktree = runtime.worktree_snapshot();
            if json {
                println!("{}", serde_json::to_string_pretty(&worktree)?);
            } else {
                print_worktree_snapshot(&worktree);
            }
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
        "/remember" => {
            let memory = command_body.trim();
            if memory.is_empty() {
                println!("Usage: /remember <text to add to workspace knowledge>");
                return Ok(true);
            }
            let source = local_result(
                runtime.add_text_knowledge_source(state.workspace_id.clone(), memory),
            )?;
            println!("Remembered workspace knowledge");
            println!("  id:   {}", source.source_id);
            println!("  file: {}", source.path);
            Ok(true)
        }
        "/forget" => {
            let Some(source_id) = parts.next() else {
                println!("Usage: /forget <source_id>");
                return Ok(true);
            };
            let source = local_result(runtime.remove_knowledge_source(source_id))?;
            println!("Forgot workspace knowledge");
            println!("  id:   {}", source.source_id);
            println!("  file: {}", source.path);
            Ok(true)
        }
        "/recall" => {
            let Some(source_id) = parts.next() else {
                println!("Usage: /recall <source_id>");
                return Ok(true);
            };
            let preview =
                local_result(runtime.read_knowledge_source(source_id, PREVIEW_MAX_BYTES))?;
            print_knowledge_preview(&preview);
            Ok(true)
        }
        "/sources" => {
            let sources =
                local_result(runtime.knowledge_sources(state.workspace_id.as_deref(), 8))?;
            print_knowledge_sources(&sources);
            Ok(true)
        }
        "/rate" => {
            let Some(source_id) = parts.next() else {
                println!("Usage: /rate <source_id> <1-5> [note]");
                return Ok(true);
            };
            let Some(rating) = parts.next() else {
                println!("Usage: /rate <source_id> <1-5> [note]");
                return Ok(true);
            };
            let rating = match rating.parse::<u8>() {
                Ok(rating) => rating,
                Err(_) => {
                    println!("Rating must be a number from 1 to 5.");
                    return Ok(true);
                }
            };
            let note = parts.collect::<Vec<_>>().join(" ");
            let source_rating = local_result(runtime.rate_knowledge_source(
                source_id,
                state.last_run_id.as_deref(),
                rating,
                &note,
            ))?;
            print_source_rating(&source_rating);
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
        "/select" => {
            let Some(run_id) = parts.next() else {
                println!("Usage: /select <run_id>");
                return Ok(true);
            };
            let run = local_result(runtime.run_by_id(run_id))?;
            state.last_run_id = Some(run.run_id.clone());
            print_run_summary(&run);
            Ok(true)
        }
        "/last" => {
            let Some(run_id) = state.last_run_id.as_deref() else {
                println!("No run selected. Run a prompt or use /select <run_id>.");
                return Ok(true);
            };
            let run = local_result(runtime.run_by_id(run_id))?;
            print_run_summary(&run);
            Ok(true)
        }
        "/continue" | "/resume" | "/again" => {
            run_continuation_from_session(runtime, repo_root, json, state, command_body)?;
            Ok(true)
        }
        "/retry" => {
            let run_id = parts
                .next()
                .map(str::to_string)
                .or_else(|| state.last_run_id.clone());
            let Some(run_id) = run_id else {
                println!("No run selected. Run a prompt or use /retry <run_id>.");
                return Ok(true);
            };
            let body = format!("{run_id} {}", retry_instruction());
            run_continuation_from_session(runtime, repo_root, json, state, &body)?;
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
        "/gc" => {
            let args = parts.collect::<Vec<_>>();
            let (run_id, retain_last) = match args.as_slice() {
                [first, rest @ ..] if first.starts_with("run_") => (
                    Some((*first).to_string()),
                    rest.first().and_then(|value| value.parse::<usize>().ok()),
                ),
                [first, ..] => (state.last_run_id.clone(), first.parse::<usize>().ok()),
                [] => (state.last_run_id.clone(), None),
            };
            let Some(run_id) = run_id else {
                println!("No run selected. Run a prompt or use /gc <run_id> [retain_last].");
                return Ok(true);
            };
            let preview = local_result(runtime.run_event_gc_preview(&run_id, retain_last))?;
            print_event_gc_preview(&preview);
            Ok(true)
        }
        "/tools" | "/tool-trace" => {
            let run_id = parts
                .next()
                .map(str::to_string)
                .or_else(|| state.last_run_id.clone());
            let Some(run_id) = run_id else {
                println!("No run selected. Run a prompt or use /tools <run_id>.");
                return Ok(true);
            };
            let trace = local_result(runtime.run_tool_trace(&run_id))?;
            print_tool_trace(&trace);
            Ok(true)
        }
        "/plan" => {
            let run_id = parts
                .next()
                .map(str::to_string)
                .or_else(|| state.last_run_id.clone());
            let Some(run_id) = run_id else {
                println!("No run selected. Run a prompt or use /plan <run_id>.");
                return Ok(true);
            };
            let plan = local_result(runtime.run_plan(&run_id))?;
            print_run_plan(&plan);
            Ok(true)
        }
        "/trace" | "/core-trace" => {
            let run_id = parts
                .next()
                .map(str::to_string)
                .or_else(|| state.last_run_id.clone());
            let Some(run_id) = run_id else {
                println!("No run selected. Run a prompt or use /trace <run_id>.");
                return Ok(true);
            };
            let trace = local_result(runtime.run_core_trace(&run_id))?;
            print_run_core_trace(&trace);
            Ok(true)
        }
        "/review" => {
            let run_id = parts
                .next()
                .map(str::to_string)
                .or_else(|| state.last_run_id.clone());
            let Some(run_id) = run_id else {
                println!("No run selected. Run a prompt or use /review <run_id>.");
                return Ok(true);
            };
            let review = local_result(runtime.run_review(&run_id))?;
            print_run_review(&review);
            Ok(true)
        }
        "/compact" => {
            let run_id = parts
                .next()
                .map(str::to_string)
                .or_else(|| state.last_run_id.clone());
            let Some(run_id) = run_id else {
                println!("No run selected. Run a prompt or use /compact <run_id>.");
                return Ok(true);
            };
            let compact = local_result(runtime.run_compact(&run_id))?;
            print_run_compact(&compact);
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
        "/usage" => {
            let run_id = parts
                .next()
                .map(str::to_string)
                .or_else(|| state.last_run_id.clone());
            let Some(run_id) = run_id else {
                println!("No run selected. Run a prompt or use /usage <run_id>.");
                return Ok(true);
            };
            let evidence = local_result(runtime.run_evidence_summary(&run_id))?;
            print_model_usage_summary(&evidence);
            Ok(true)
        }
        "/transcript" | "/inspect" => {
            let run_id = parts
                .next()
                .map(str::to_string)
                .or_else(|| state.last_run_id.clone());
            let Some(run_id) = run_id else {
                println!("No run selected. Run a prompt or use /transcript <run_id>.");
                return Ok(true);
            };
            let transcript = local_result(runtime.run_transcript(&run_id))?;
            state.last_run_id = Some(transcript.run.run_id.clone());
            print_run_transcript(&transcript);
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
        "/proposal-review" | "/risk" => {
            let selected = parts.next().map(str::to_string);
            let artifact_id = if let Some(value) = selected {
                if value.starts_with("art_") {
                    value
                } else {
                    local_result(latest_proposal_preview(
                        runtime,
                        state.workspace_id.as_deref(),
                        Some(&value),
                        PREVIEW_MAX_BYTES,
                    ))?
                    .artifact
                    .artifact_id
                }
            } else {
                local_result(latest_proposal_preview(
                    runtime,
                    state.workspace_id.as_deref(),
                    state.last_run_id.as_deref(),
                    PREVIEW_MAX_BYTES,
                ))?
                .artifact
                .artifact_id
            };
            let review = local_result(runtime.review_code_change_proposal(&artifact_id))?;
            print_proposal_review(&review);
            Ok(true)
        }
        "/dry-run" => {
            let args = parts.collect::<Vec<_>>();
            let selected = args.first().map(|value| (*value).to_string());
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
            let result = local_result(runtime.apply_code_change_proposal(&artifact_id, true))?;
            print!(
                "{}",
                render_proposal_apply_result(&result, result.dry_run, run_id.as_deref())
            );
            Ok(true)
        }
        "/apply" => {
            let args = parts.collect::<Vec<_>>();
            let apply_args = parse_session_apply_args(&args);
            let (artifact_id, run_id) = match apply_args.selected {
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
            let result =
                local_result(runtime.apply_code_change_proposal(&artifact_id, apply_args.dry_run))?;
            print!(
                "{}",
                render_proposal_apply_result(&result, result.dry_run, run_id.as_deref())
            );
            Ok(true)
        }
        "/replay" => {
            let replay = local_result(runtime.workspace_replay(state.workspace_id.as_deref(), 40))?;
            print_workspace_replay(&replay);
            Ok(true)
        }
        "/session" | "/workspace-compact" => {
            let compact =
                local_result(runtime.workspace_compact(state.workspace_id.as_deref(), 12))?;
            print_workspace_compact(&compact);
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

fn run_continuation_from_session(
    runtime: &LocalAgentRuntime,
    repo_root: &PathBuf,
    json: bool,
    state: &mut ChatSessionState,
    command_body: &str,
) -> Result<()> {
    let (run_id, extra_instruction) =
        parse_continuation_args(state.last_run_id.clone(), command_body);
    let Some(run_id) = run_id else {
        println!("No run selected. Run a prompt or use /continue <run_id> [instruction].");
        return Ok(());
    };
    let transcript = local_result(runtime.run_transcript(&run_id))?;
    let workspace_id = transcript.run.workspace_id;
    state.workspace_id = Some(workspace_id.clone());
    let request = ContinuationRequest {
        run_id,
        extra_instruction,
        mode: Some(state.mode.clone()),
    };
    let attempt = if json {
        local_result(runtime.run_continuation_attempt(request))?
    } else {
        run_continuation_with_live_events(repo_root, workspace_id, request)?
    };
    state.last_run_id = Some(attempt.run.run_id.clone());
    if json {
        println!("{}", serde_json::to_string_pretty(&attempt)?);
    } else {
        print_chat_attempt(&attempt);
    }
    Ok(())
}

fn retry_instruction() -> &'static str {
    "Retry the previous request as a fresh Structure local run. Use the persisted transcript, events, evidence, workspace context, and current repository state; do not mutate the old run."
}

fn parse_continuation_args(
    selected_run_id: Option<String>,
    command_body: &str,
) -> (Option<String>, Option<String>) {
    let command_body = command_body.trim();
    if command_body.is_empty() {
        return (selected_run_id, None);
    }

    let mut parts = command_body.splitn(2, char::is_whitespace);
    let first = parts.next().unwrap_or_default();
    if first.starts_with("run_") {
        let extra = parts
            .next()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string);
        (Some(first.to_string()), extra)
    } else {
        (selected_run_id, Some(command_body.to_string()))
    }
}

fn parse_agent_mode(value: Option<&str>, chat_only: bool) -> Result<LocalAgentMode> {
    match value.map(str::trim).filter(|value| !value.is_empty()) {
        Some("chat") => Ok(LocalAgentMode::Chat),
        Some("code") | Some("code_agent") | Some("code-agent") => Ok(LocalAgentMode::CodeAgent),
        Some(other) => Err(anyhow!(
            "unknown agent mode `{other}`; use `chat`, `code`, or `code_agent`"
        )),
        None if chat_only => Ok(LocalAgentMode::Chat),
        None => Ok(LocalAgentMode::CodeAgent),
    }
}

fn print_chat_session_help() {
    println!("Structure local session commands");
    println!("  @path[:line] prompt    Attach a repo-relative file before model planning");
    println!("  /status               Show workspace, mode, selected run, and LLM env");
    println!("  /llm                  Check the configured OPENAI__ API endpoint");
    println!("  /doctor               Check local runtime, OPENAI__, and Core parity");
    println!("  /context              Show assembled agent context without starting a run");
    println!("  /worktree             Show current branch and changed files");
    println!("  /dirty                Alias for /worktree");
    println!("  /mode chat|code       Switch between chat and code-agent mode");
    println!("  /workspace [id]       Show or open/create a workspace");
    println!("  /ls                   List top-level workspace entries");
    println!("  /search <query>       Search repo text through local tools");
    println!("  /read <path>          Read a repo-relative file safely");
    println!("  /cmd <argv...>        Run an allowlisted local check command");
    println!("  /source <path>        Register a knowledge file for this workspace");
    println!("  /remember <text>      Persist text as local workspace knowledge");
    println!("  /forget <source_id>   Remove a workspace knowledge source");
    println!("  /recall <source_id>   Preview a workspace knowledge source");
    println!("  /sources              List workspace knowledge sources");
    println!("  /rate <src> <1-5>     Rate a source for the selected run");
    println!("  /runs                 List recent runs in this workspace");
    println!("  /select <run_id>      Select a run for follow-up inspection");
    println!("  /last                 Show the selected or latest run summary");
    println!("  /continue [run] [msg] Continue from a selected or explicit run");
    println!("  /resume [run] [msg]   Alias for /continue");
    println!("  /retry [run_id]       Retry a selected or explicit run as a fresh run");
    println!("  /events [run_id]      Show event stream for a run");
    println!("  /gc [run_id] [n]      Preview non-destructive event retention");
    println!("  /tools [run_id]       Show paired local tool calls and results");
    println!("  /plan [run_id]        Show the event-derived agent plan");
    println!("  /trace [run_id]       Show Structure Core flow/primitive execution path");
    println!("  /review [run_id]      Show attempt review and next actions");
    println!("  /compact [run_id]     Show compact context for continuing a run");
    println!("  /evidence [run_id]    Show run evidence summary");
    println!("  /usage [run_id]       Show model requests, network calls, and token usage");
    println!("  /transcript [run_id]  Show run, chat turn, events, evidence, and response");
    println!("  /inspect [run_id]     Alias for /transcript");
    println!("  /artifacts            List recent artifacts");
    println!("  /proposal [run_id]    Show the latest code-change proposal");
    println!("  /diff [run_id]        Alias for /proposal");
    println!("  /risk [id|run]        Review proposal target, patch checks, and apply risk");
    println!("  /dry-run [id|run]     Preview proposal application without writing");
    println!("  /apply [id|run]       Dry-run a proposal; add --yes to apply after review");
    println!("  /replay               Replay workspace event stream");
    println!("  /session              Show workspace/session compact context");
    println!("  /quit                 Exit");
}

fn chat_session_command_summary() -> &'static str {
    "/help, /status, /llm, /doctor, /context, /worktree, /mode, /workspace, /ls, /search, /read, /source, /remember, /recall, /forget, /rate, /runs, /events, /gc, /tools, /plan, /trace, /review, /compact, /session, /continue, /retry, /usage, /transcript, /proposal, /diff, /risk, /dry-run, /apply, /quit"
}

fn render_chat_session_status(state: &ChatSessionState, snapshot: &LocalSnapshot) -> String {
    let config = &snapshot.llm_config;
    let mut text = String::new();
    text.push_str("Structure local session\n");
    text.push_str(&format!(
        "  workspace:    {}\n",
        state.workspace_id.as_deref().unwrap_or("default")
    ));
    text.push_str(&format!(
        "  mode:         {}\n",
        session_mode_label(&state.mode)
    ));
    text.push_str(&format!(
        "  selected run: {}\n",
        state.last_run_id.as_deref().unwrap_or("none")
    ));
    text.push_str(&format!("  repo:         {}\n", snapshot.repo_root));
    text.push_str(&format!("  runtime:      {}\n", snapshot.runtime_dir));
    text.push_str(&format!(
        "  llm:          {} ({}/{}/{})\n",
        if config.configured {
            "OPENAI__ configured"
        } else {
            "OPENAI__ missing"
        },
        config.api_key.source,
        config.base_url.source,
        config.model.source
    ));
    text.push_str(&format!(
        "  model:        {}\n",
        config.model_name.as_deref().unwrap_or("not set")
    ));
    text
}

fn render_chat_session_doctor(
    state: &ChatSessionState,
    snapshot: &LocalSnapshot,
    diagnostic: &LocalLlmDiagnostic,
    parity: &structure_local_core::SurfaceParityReport,
) -> String {
    let passed_checks = parity.checks.iter().filter(|check| check.passed).count();
    let failed_checks = parity.checks.len().saturating_sub(passed_checks);
    let mut text = String::new();
    text.push_str("Structure local doctor\n");
    text.push_str(&format!(
        "  workspace:    {}\n",
        state.workspace_id.as_deref().unwrap_or("default")
    ));
    text.push_str(&format!(
        "  mode:         {}\n",
        session_mode_label(&state.mode)
    ));
    text.push_str(&format!("  repo:         {}\n", snapshot.repo_root));
    text.push_str(&format!("  runtime:      {}\n", snapshot.runtime_dir));
    text.push_str(&format!(
        "  llm:          {} / {}\n",
        if diagnostic.ok { "ok" } else { "not ok" },
        diagnostic.provider
    ));
    text.push_str(&format!("  configured:   {}\n", diagnostic.configured));
    text.push_str(&format!(
        "  model:        {}\n",
        diagnostic.model.as_deref().unwrap_or("not set")
    ));
    text.push_str(&format!(
        "  endpoint:     {}\n",
        diagnostic.endpoint.as_deref().unwrap_or("not set")
    ));
    text.push_str(&format!(
        "  core parity: {}\n",
        if parity.passed {
            "passed"
        } else {
            "needs attention"
        }
    ));
    text.push_str(&format!("  schema:       {}\n", parity.schema_version));
    text.push_str(&format!("  surfaces:     {}\n", parity.surface_count));
    text.push_str(&format!("  primitives:   {}\n", parity.primitive_count));
    text.push_str(&format!("  capabilities: {}\n", parity.capability_count));
    text.push_str(&format!(
        "  checks:       {} ok / {} fail\n",
        passed_checks, failed_checks
    ));
    if let Some(error) = &diagnostic.error {
        text.push_str(&format!("  error:        {}\n", error));
    }
    text
}

fn render_local_doctor_report(
    diagnostic: &LocalLlmDiagnostic,
    parity: &structure_local_core::SurfaceParityReport,
    context: &LocalAgentContext,
) -> String {
    let passed_checks = parity.checks.iter().filter(|check| check.passed).count();
    let failed_checks = parity.checks.len().saturating_sub(passed_checks);
    let mut text = String::new();
    text.push_str("Structure local doctor\n");
    text.push_str(&format!("  workspace:    {}\n", context.workspace_id));
    text.push_str(&format!("  mode:         {}\n", context.mode));
    text.push_str(&format!("  repo:         {}\n", context.repo_root));
    text.push_str(&format!("  runtime db:   {}\n", context.runtime_db));
    text.push_str(&format!(
        "  llm:          {} / {}\n",
        if diagnostic.ok { "ok" } else { "not ok" },
        diagnostic.provider
    ));
    text.push_str(&format!("  configured:   {}\n", diagnostic.configured));
    text.push_str(&format!(
        "  model:        {}\n",
        diagnostic.model.as_deref().unwrap_or("not set")
    ));
    text.push_str(&format!(
        "  endpoint:     {}\n",
        diagnostic.endpoint.as_deref().unwrap_or("not set")
    ));
    text.push_str(&format!(
        "  core parity: {}\n",
        if parity.passed {
            "passed"
        } else {
            "needs attention"
        }
    ));
    text.push_str(&format!(
        "  checks:       {} ok / {} fail\n",
        passed_checks, failed_checks
    ));
    text.push_str(&format!(
        "  context:      {} instruction(s), {} knowledge source(s), {} recent turn(s)\n",
        context.agent_instructions.len(),
        context.knowledge_sources.len(),
        context.recent_turns.len()
    ));
    text.push_str(&format!(
        "  worktree:     {} / {} change(s)\n",
        if context.worktree.clean {
            "clean"
        } else {
            "dirty"
        },
        context.worktree.changed_files.len()
    ));
    if let Some(error) = &diagnostic.error {
        text.push_str(&format!("  error:        {}\n", error));
    }
    text
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
        RunsCommand::Plan(args) => {
            let plan = local_result(runtime.run_plan(&args.run_id))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&plan)?);
            } else {
                print_run_plan(&plan);
            }
        }
        RunsCommand::Trace(args) => {
            let trace = local_result(runtime.run_core_trace(&args.run_id))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&trace)?);
            } else {
                print_run_core_trace(&trace);
            }
        }
        RunsCommand::Review(args) => {
            let review = local_result(runtime.run_review(&args.run_id))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&review)?);
            } else {
                print_run_review(&review);
            }
        }
        RunsCommand::Compact(args) => {
            let compact = local_result(runtime.run_compact(&args.run_id))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&compact)?);
            } else {
                print_run_compact(&compact);
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
        RunsCommand::Transcript(args) => {
            let transcript = local_result(runtime.run_transcript(&args.run_id))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&transcript)?);
            } else {
                print_run_transcript(&transcript);
            }
        }
        RunsCommand::Inspect(args) => {
            let transcript = local_result(runtime.run_transcript(&args.run_id))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&transcript)?);
            } else {
                print_run_transcript(&transcript);
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
        WorkspaceCommand::Compact(args) => {
            let compact =
                local_result(runtime.workspace_compact(args.workspace.as_deref(), args.limit))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&compact)?);
            } else {
                print_workspace_compact(&compact);
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
        ProposalsCommand::Review(args) => {
            let review = local_result(runtime.review_code_change_proposal(&args.artifact_id))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&review)?);
            } else {
                print_proposal_review(&review);
            }
        }
        ProposalsCommand::Apply(args) => {
            let result =
                local_result(runtime.apply_code_change_proposal(&args.artifact_id, args.dry_run))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                print!(
                    "{}",
                    render_proposal_apply_result(&result, result.dry_run, None)
                );
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

#[derive(Debug, PartialEq, Eq)]
struct SessionApplyArgs {
    dry_run: bool,
    selected: Option<String>,
}

fn parse_session_apply_args(args: &[&str]) -> SessionApplyArgs {
    let explicit_dry_run = args.contains(&"--dry-run");
    let approved = args.contains(&"--yes");
    let selected = args
        .iter()
        .find(|part| !matches!(**part, "--dry-run" | "--yes"))
        .map(|part| (*part).to_string());
    SessionApplyArgs {
        dry_run: explicit_dry_run || !approved,
        selected,
    }
}

fn render_proposal_apply_result(
    result: &ProposalApplyResult,
    include_preview: bool,
    selected_run_id: Option<&str>,
) -> String {
    let mut text = String::new();
    text.push_str(&format!(
        "{} proposal {}\n",
        if result.applied {
            "Applied"
        } else {
            "Previewed"
        },
        result.artifact.artifact_id
    ));
    text.push_str(&format!(
        "  run:         {}\n",
        selected_run_id.unwrap_or(&result.artifact.run_id)
    ));
    text.push_str(&format!("  target:      {}\n", result.target_path));
    text.push_str(&format!("  added lines: {}\n", result.added_lines));
    text.push_str(&format!("  bytes:       {}\n", result.bytes_written));
    text.push_str(&format!("  dry run:     {}\n", result.dry_run));
    if include_preview {
        text.push('\n');
        if result.dry_run {
            text.push_str("Review this preview, then rerun /apply with --yes to write.\n\n");
        }
        text.push_str(&result.preview);
        if !text.ends_with('\n') {
            text.push('\n');
        }
    }
    text
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::CommandFactory;
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn cli_dry_run_apply_output_includes_review_preview() {
        let result = ProposalApplyResult {
            artifact: structure_local_runtime::ArtifactRecord {
                artifact_id: "art_review".to_string(),
                run_id: "run_review".to_string(),
                workspace_id: "workspace".to_string(),
                kind: "code_change_proposal".to_string(),
                path: "artifacts/code_change_proposal.md".to_string(),
                size_bytes: 128,
                created_at_ms: 1,
            },
            target_path: "docs/local-code-agent-proposal.md".to_string(),
            applied: false,
            dry_run: true,
            preview: "Patch preview\n+Evidence line".to_string(),
            added_lines: 1,
            bytes_written: 0,
        };

        let output = render_proposal_apply_result(&result, true, None);

        assert!(output.contains("Previewed proposal art_review"));
        assert!(output.contains("run:         run_review"));
        assert!(output.contains("Patch preview"));
        assert!(output.contains("+Evidence line"));
        assert!(output.contains("rerun /apply with --yes"));
    }

    #[test]
    fn cli_session_apply_defaults_to_dry_run_until_yes() {
        assert_eq!(
            parse_session_apply_args(&[]),
            SessionApplyArgs {
                dry_run: true,
                selected: None
            }
        );
        assert_eq!(
            parse_session_apply_args(&["run_1"]),
            SessionApplyArgs {
                dry_run: true,
                selected: Some("run_1".to_string())
            }
        );
        assert_eq!(
            parse_session_apply_args(&["run_1", "--yes"]),
            SessionApplyArgs {
                dry_run: false,
                selected: Some("run_1".to_string())
            }
        );
        assert_eq!(
            parse_session_apply_args(&["art_1", "--yes", "--dry-run"]),
            SessionApplyArgs {
                dry_run: true,
                selected: Some("art_1".to_string())
            }
        );
    }

    #[test]
    fn cli_continue_args_use_selected_run_or_explicit_run_id() {
        assert_eq!(
            parse_continuation_args(Some("run_selected".to_string()), ""),
            (Some("run_selected".to_string()), None)
        );
        assert_eq!(
            parse_continuation_args(Some("run_selected".to_string()), "add tests"),
            (
                Some("run_selected".to_string()),
                Some("add tests".to_string())
            )
        );
        assert_eq!(
            parse_continuation_args(Some("run_selected".to_string()), "run_other add docs"),
            (Some("run_other".to_string()), Some("add docs".to_string()))
        );
        assert_eq!(parse_continuation_args(None, ""), (None, None));
    }

    #[test]
    fn cli_continue_command_accepts_noninteractive_instruction_and_mode() {
        let cli = Cli::try_parse_from([
            "structure-local",
            "continue",
            "run_1",
            "write",
            "tests",
            "--mode",
            "chat",
            "--json",
        ])
        .unwrap();

        let Command::Continue(args) = cli.command else {
            panic!("expected continue command");
        };
        assert_eq!(args.run_id, "run_1");
        assert_eq!(args.instruction, vec!["write", "tests"]);
        assert_eq!(args.mode.as_deref(), Some("chat"));
        assert!(args.json);
    }

    #[test]
    fn cli_retry_command_accepts_run_mode_and_json() {
        let cli = Cli::try_parse_from([
            "structure-local",
            "retry",
            "run_1",
            "--mode",
            "code-agent",
            "--json",
        ])
        .unwrap();

        let Command::Retry(args) = cli.command else {
            panic!("expected retry command");
        };
        assert_eq!(args.run_id, "run_1");
        assert_eq!(args.mode.as_deref(), Some("code-agent"));
        assert!(args.json);
    }

    #[test]
    fn cli_runs_plan_command_accepts_json_output() {
        let cli =
            Cli::try_parse_from(["structure-local", "runs", "plan", "run_1", "--json"]).unwrap();

        let Command::Runs {
            command: RunsCommand::Plan(args),
        } = cli.command
        else {
            panic!("expected runs plan command");
        };
        assert_eq!(args.run_id, "run_1");
        assert!(args.json);
    }

    #[test]
    fn cli_runs_compact_command_accepts_json_output() {
        let cli =
            Cli::try_parse_from(["structure-local", "runs", "compact", "run_1", "--json"]).unwrap();

        let Command::Runs {
            command: RunsCommand::Compact(args),
        } = cli.command
        else {
            panic!("expected runs compact command");
        };
        assert_eq!(args.run_id, "run_1");
        assert!(args.json);
    }

    #[test]
    fn cli_workspace_compact_command_accepts_json_output() {
        let cli = Cli::try_parse_from([
            "structure-local",
            "workspace",
            "compact",
            "--workspace",
            "paper",
            "--limit",
            "4",
            "--json",
        ])
        .unwrap();

        let Command::Workspace {
            command: WorkspaceCommand::Compact(args),
        } = cli.command
        else {
            panic!("expected workspace compact command");
        };
        assert_eq!(args.workspace.as_deref(), Some("paper"));
        assert_eq!(args.limit, 4);
        assert!(args.json);
    }

    #[test]
    fn cli_proposals_review_command_accepts_json_output() {
        let cli =
            Cli::try_parse_from(["structure-local", "proposals", "review", "art_1", "--json"])
                .unwrap();

        let Command::Proposals {
            command: ProposalsCommand::Review(args),
        } = cli.command
        else {
            panic!("expected proposals review command");
        };
        assert_eq!(args.artifact_id, "art_1");
        assert!(args.json);
    }

    #[test]
    fn cli_chat_session_summary_includes_worktree_command() {
        let summary = chat_session_command_summary();

        assert!(summary.contains("/worktree"));
        assert!(summary.contains("/status"));
        assert!(summary.contains("/doctor"));
        assert!(summary.contains("/context"));
        assert!(summary.contains("/continue"));
        assert!(summary.contains("/retry"));
        assert!(summary.contains("/usage"));
        assert!(summary.contains("/remember"));
        assert!(summary.contains("/recall"));
        assert!(summary.contains("/forget"));
        assert!(summary.contains("/rate"));
        assert!(summary.contains("/events"));
        assert!(summary.contains("/gc"));
        assert!(summary.contains("/tools"));
        assert!(summary.contains("/plan"));
        assert!(summary.contains("/trace"));
        assert!(summary.contains("/review"));
        assert!(summary.contains("/compact"));
        assert!(summary.contains("/session"));
        assert!(summary.contains("/diff"));
        assert!(summary.contains("/risk"));
        assert!(summary.contains("/dry-run"));
        assert!(!summary.contains("benchmark"));
    }

    #[test]
    fn cli_worktree_command_accepts_json_output() {
        let cli = Cli::try_parse_from(["structure-local", "worktree", "--json"]).unwrap();

        let Command::Worktree(args) = cli.command else {
            panic!("expected worktree command");
        };
        assert!(args.json);
    }

    #[test]
    fn cli_context_command_accepts_workspace_mode_and_json() {
        let cli = Cli::try_parse_from([
            "structure-local",
            "context",
            "--workspace",
            "paper",
            "--mode",
            "chat",
            "--json",
        ])
        .unwrap();

        let Command::Context(args) = cli.command else {
            panic!("expected context command");
        };
        assert_eq!(args.workspace.as_deref(), Some("paper"));
        assert_eq!(args.mode.as_deref(), Some("chat"));
        assert!(args.json);
    }

    #[test]
    fn cli_doctor_command_accepts_workspace_mode_and_json() {
        let cli = Cli::try_parse_from([
            "structure-local",
            "doctor",
            "--workspace",
            "default",
            "--mode",
            "code-agent",
            "--json",
        ])
        .unwrap();

        let Command::Doctor(args) = cli.command else {
            panic!("expected doctor command");
        };
        assert_eq!(args.workspace.as_deref(), Some("default"));
        assert_eq!(args.mode.as_deref(), Some("code-agent"));
        assert!(args.json);
    }

    #[test]
    fn cli_help_keeps_benchmarks_out_of_local_agent_surface() {
        let mut command = Cli::command();
        let mut help = Vec::new();
        command.write_long_help(&mut help).unwrap();
        let help = String::from_utf8(help).unwrap().to_lowercase();

        assert!(!help.contains("benchmark"));
        assert!(!help.contains("bench"));
    }

    #[test]
    fn cli_runs_help_exposes_inspect_alias_for_transcripts() {
        let runs = Cli::command()
            .find_subcommand("runs")
            .expect("runs command should exist")
            .clone();

        assert!(runs.find_subcommand("transcript").is_some());
        assert!(runs.find_subcommand("inspect").is_some());
    }

    #[test]
    fn cli_agent_mode_defaults_to_code_agent() {
        assert_eq!(
            parse_agent_mode(None, false).unwrap(),
            LocalAgentMode::CodeAgent
        );
    }

    #[test]
    fn cli_agent_mode_keeps_chat_only_compatibility() {
        assert_eq!(parse_agent_mode(None, true).unwrap(), LocalAgentMode::Chat);
    }

    #[test]
    fn cli_agent_mode_accepts_chat_and_code_aliases() {
        assert_eq!(
            parse_agent_mode(Some("chat"), false).unwrap(),
            LocalAgentMode::Chat
        );
        assert_eq!(
            parse_agent_mode(Some("code"), false).unwrap(),
            LocalAgentMode::CodeAgent
        );
        assert_eq!(
            parse_agent_mode(Some("code_agent"), false).unwrap(),
            LocalAgentMode::CodeAgent
        );
        assert_eq!(
            parse_agent_mode(Some("code-agent"), false).unwrap(),
            LocalAgentMode::CodeAgent
        );
    }

    #[test]
    fn cli_agent_mode_rejects_unknown_values() {
        let error = parse_agent_mode(Some("planner"), false)
            .expect_err("unknown mode should be rejected")
            .to_string();

        assert!(error.contains("unknown agent mode"));
    }

    #[test]
    fn cli_session_status_reports_workspace_mode_and_llm_contract() {
        let root = unique_repo("cli-session-status");
        fs::write(
            root.join(".env"),
            "OPENAI__API_KEY=test-key\nOPENAI__BASE_URL=http://example.test/v1\nOPENAI__MODEL=test-model\n",
        )
        .unwrap();
        let snapshot = collect_snapshot(&root).unwrap();
        let state = ChatSessionState {
            workspace_id: Some("paper".to_string()),
            mode: LocalAgentMode::CodeAgent,
            last_run_id: Some("run_selected".to_string()),
        };

        let output = render_chat_session_status(&state, &snapshot);

        assert!(output.contains("workspace:    paper"));
        assert!(output.contains("mode:         code_agent"));
        assert!(output.contains("selected run: run_selected"));
        assert!(output.contains("llm:          OPENAI__"));
        assert!(output.contains("model:        "));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn cli_llm_diagnostic_output_uses_openai_contract_without_secret() {
        let diagnostic = LocalLlmDiagnostic {
            provider: "local_env_api".to_string(),
            configured: true,
            ok: true,
            model: Some("test-model".to_string()),
            endpoint: Some("http://example.test/v1/chat/completions".to_string()),
            elapsed_ms: 12,
            response_preview: Some("structure-local-ok".to_string()),
            error: None,
        };

        let output = render_llm_diagnostic(&diagnostic);

        assert!(output.contains("Structure local LLM diagnostic"));
        assert!(output.contains("provider:   local_env_api"));
        assert!(output.contains("configured: true"));
        assert!(output.contains("ok:         true"));
        assert!(output.contains("model:      test-model"));
        assert!(output.contains("structure-local-ok"));
        assert!(!output.contains("OPENAI__API_KEY"));
    }

    fn unique_repo(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "structure-cli-{label}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(root.join("src/structure")).unwrap();
        fs::create_dir_all(root.join("frontend")).unwrap();
        fs::write(root.join("pyproject.toml"), "").unwrap();
        root.canonicalize().unwrap()
    }
}
