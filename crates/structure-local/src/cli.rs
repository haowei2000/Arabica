use crate::text::{
    print_artifact_preview, print_artifacts, print_core_capabilities, print_core_manifest,
    print_events, print_knowledge_preview, print_knowledge_source, print_knowledge_sources,
    print_local_evidence_bundle, print_run_evidence_summary, print_run_result, print_run_summary,
    print_runs, print_snapshot, print_surface_parity_report, print_surfaces, print_workspace,
    print_workspace_replay, print_workspaces,
};
use crate::tui;
use anyhow::{anyhow, Result};
use clap::{Args, Parser, Subcommand};
use std::io::{self, Write};
use std::path::PathBuf;
use structure_local_core::{
    collect_snapshot, default_repo_root, product_surfaces, snapshot_json, structure_core_manifest,
};
use structure_local_runtime::{LocalAgentMode, LocalAgentRuntime, RunRequest};

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
    Show(ShowProposalArgs),
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
struct ShowProposalArgs {
    artifact_id: String,
    #[arg(long, default_value_t = PREVIEW_MAX_BYTES)]
    max_bytes: u64,
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
    let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    let result = local_result(runtime.run_prompt(RunRequest {
        prompt: args.prompt,
        workspace_id: args.workspace,
        mode: Some(LocalAgentMode::CodeAgent),
    }))?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&result)?);
    } else {
        print_run_result(&result);
    }
    Ok(())
}

fn run_chat_agent(repo_root: &PathBuf, args: ChatArgs) -> Result<()> {
    let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
    let mode = if args.chat_only {
        LocalAgentMode::Chat
    } else {
        LocalAgentMode::CodeAgent
    };
    let workspace_id = args.workspace.clone();
    if let Some(prompt) = args.prompt {
        let result = local_result(runtime.run_prompt(RunRequest {
            prompt,
            workspace_id,
            mode: Some(mode),
        }))?;
        if args.json {
            println!("{}", serde_json::to_string_pretty(&result)?);
        } else {
            print_chat_turn(&result);
        }
        return Ok(());
    }

    println!("Structure local chat");
    println!(
        "  workspace: {}",
        workspace_id.as_deref().unwrap_or("default")
    );
    println!("  mode:      {}", mode.as_str());
    println!("  exit:      /quit");
    println!();

    let stdin = io::stdin();
    loop {
        print!("structure> ");
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
        if matches!(prompt, "/quit" | "/exit") {
            break;
        }
        let result = local_result(runtime.run_prompt(RunRequest {
            prompt: prompt.to_string(),
            workspace_id: workspace_id.clone(),
            mode: Some(mode.clone()),
        }))?;
        if args.json {
            println!("{}", serde_json::to_string_pretty(&result)?);
        } else {
            print_chat_turn(&result);
        }
    }
    Ok(())
}

fn print_chat_turn(result: &structure_local_runtime::RunResult) {
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
    }
    Ok(())
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
