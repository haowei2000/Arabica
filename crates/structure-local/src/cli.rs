use crate::text::{
    print_artifact_preview, print_artifacts, print_core_capabilities, print_core_manifest,
    print_events, print_knowledge_preview, print_knowledge_source, print_knowledge_sources,
    print_local_benchmark_evidence, print_local_evidence_bundle, print_run_evidence_summary,
    print_run_result, print_run_summary, print_runs, print_snapshot, print_surface_parity_report,
    print_surfaces, print_workspace, print_workspace_replay, print_workspaces,
};
use crate::tui;
use anyhow::{anyhow, Result};
use clap::{Args, Parser, Subcommand};
use std::path::PathBuf;
use std::process::{Command as ProcessCommand, Stdio};
use structure_local_core::{
    collect_snapshot, default_repo_root, product_surfaces, read_repo_text_file,
    recent_benchmark_reports, snapshot_json, structure_core_manifest,
};
use structure_local_runtime::{LocalAgentRuntime, LocalBenchmarkRequest, RunRequest};

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
    Evidence {
        #[command(subcommand)]
        command: EvidenceCommand,
    },
    Bench {
        #[command(subcommand)]
        command: BenchCommand,
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
struct EvidenceBundleArgs {
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long, default_value_t = 50)]
    limit: usize,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Subcommand)]
enum BenchCommand {
    Run(RunBenchArgs),
    Local(LocalBenchArgs),
    List(ListBenchArgs),
    Show(ShowBenchArgs),
    Evidence(BenchEvidenceArgs),
}

#[derive(Debug, Args)]
struct RunBenchArgs {
    #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
    args: Vec<String>,
}

#[derive(Debug, Args)]
struct LocalBenchArgs {
    #[arg(long, default_value = "local-agent-smoke")]
    benchmark: String,
    #[arg(long)]
    max_cases: Option<usize>,
    #[arg(long)]
    output_dir: Option<PathBuf>,
    #[arg(long)]
    workspace: Option<String>,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ListBenchArgs {
    #[arg(long, default_value_t = 8)]
    limit: usize,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Args)]
struct ShowBenchArgs {
    path: PathBuf,
    #[arg(long, default_value_t = PREVIEW_MAX_BYTES)]
    max_bytes: u64,
}

#[derive(Debug, Args)]
struct BenchEvidenceArgs {
    path: PathBuf,
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
        Command::Run(args) => run_local_agent(&repo_root, args)?,
        Command::Runs { command } => run_runs(&repo_root, command)?,
        Command::Workspace { command } => run_workspace(&repo_root, command)?,
        Command::Knowledge { command } => run_knowledge(&repo_root, command)?,
        Command::Artifacts { command } => run_artifacts(&repo_root, command)?,
        Command::Evidence { command } => run_evidence(&repo_root, command)?,
        Command::Bench { command } => run_bench(&repo_root, command)?,
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
    }))?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&result)?);
    } else {
        print_run_result(&result);
    }
    Ok(())
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

fn run_bench(repo_root: &PathBuf, command: BenchCommand) -> Result<()> {
    match command {
        BenchCommand::Run(args) => {
            let status = ProcessCommand::new("uv")
                .args(build_benchmark_runner_args(&args.args))
                .current_dir(repo_root)
                .stdin(Stdio::inherit())
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit())
                .status()
                .map_err(|err| anyhow!("failed to start benchmark runner: {err}"))?;
            if !status.success() {
                return Err(anyhow!("benchmark runner exited with status {status}"));
            }
        }
        BenchCommand::Local(args) => {
            let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
            let result = local_result(runtime.run_local_benchmark(LocalBenchmarkRequest {
                benchmark: Some(args.benchmark),
                max_cases: args.max_cases,
                output_dir: args.output_dir,
                workspace_id: args.workspace,
            }))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                println!("Local benchmark finished");
                println!("  benchmark: {}", result.report.benchmark);
                println!("  cases:     {}", result.report.n_cases);
                println!("  score:     {:.4}", result.report.overall_score);
                println!("  events:    {}", result.report.total_events);
                println!("  tools:     {}", result.report.total_tool_calls);
                println!("  json:      {}", result.json_path);
                println!("  markdown:  {}", result.markdown_path);
            }
        }
        BenchCommand::List(args) => {
            let reports = local_result(recent_benchmark_reports(repo_root, args.limit))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&reports)?);
            } else if reports.is_empty() {
                println!("No local benchmark reports found.");
            } else {
                for report in reports {
                    println!("{}  {} bytes", report.relative_path, report.size_bytes);
                }
            }
        }
        BenchCommand::Show(args) => {
            let text = local_result(read_repo_text_file(repo_root, args.path, args.max_bytes))?;
            print!("{text}");
        }
        BenchCommand::Evidence(args) => {
            let runtime = local_result(LocalAgentRuntime::open(repo_root))?;
            let evidence = local_result(runtime.local_benchmark_evidence(args.path))?;
            if args.json {
                println!("{}", serde_json::to_string_pretty(&evidence)?);
            } else {
                print_local_benchmark_evidence(&evidence);
            }
        }
    }
    Ok(())
}

fn local_result<T>(result: std::result::Result<T, String>) -> Result<T> {
    result.map_err(|message| anyhow!(message))
}

fn build_benchmark_runner_args(extra_args: &[String]) -> Vec<String> {
    let mut args = vec![
        "run".to_string(),
        "python".to_string(),
        "-m".to_string(),
        "benchmarks.scripts.run_structure_benchmark".to_string(),
    ];
    args.extend(extra_args.iter().cloned());
    args
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn benchmark_runner_args_delegate_to_python_module() {
        let args = build_benchmark_runner_args(&[
            "--benchmark".to_string(),
            "locomo".to_string(),
            "--max-cases".to_string(),
            "1".to_string(),
        ]);

        assert_eq!(
            args,
            vec![
                "run",
                "python",
                "-m",
                "benchmarks.scripts.run_structure_benchmark",
                "--benchmark",
                "locomo",
                "--max-cases",
                "1",
            ]
        );
    }
}
