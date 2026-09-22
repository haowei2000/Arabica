//! `structure`: the CLI binary. `structure acp` speaks Agent Client Protocol
//! v1 over stdio; `structure -p "task"` runs one task non-interactively and
//! exits (see `docs/runtime_core_architecture.md` §11).

use clap::{Parser, Subcommand};
use structure_cli::host::{
    HostConfigArgs, HostModel, LocalRunnerPolicy, build_host_runtime, process_environment,
    resolve_provider_config,
};
use structure_cli::print::{self, OutputFormat, PrintOptions};
use structure_provider::{ApiModelProvider, ApiProviderConfig};
use structure_runner::LocalTool;

#[derive(Parser, Debug)]
#[command(name = "structure", about = "Structure coding agent host")]
struct Cli {
    #[command(flatten)]
    config: HostConfigArgs,
    #[command(subcommand)]
    command: Option<Commands>,
    /// Run one task non-interactively and exit. Pass "-" to read the task
    /// from stdin instead of the argument.
    #[arg(short = 'p', long = "print", value_name = "TASK")]
    print: Option<String>,
    /// Output format for -p: "text" (default; stdout is only the final
    /// answer) or "jsonl" (stdout is one JSON event per line, live).
    #[arg(long, value_enum, default_value_t = OutputFormat::Text)]
    output_format: OutputFormat,
    /// Allow -p to run the shell tool. Off by default: shell has no path
    /// confinement, and -p has no one to ask before a call runs.
    #[arg(long)]
    allow_shell: bool,
    /// Restrict -p to read-only tools. Mutually exclusive with --allow-shell.
    #[arg(long)]
    read_only: bool,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Run as an Agent Client Protocol v1 agent over stdio (for editors like Zed).
    Acp,
}

#[tokio::main]
async fn main() {
    let cli = Cli::parse();
    std::process::exit(run(cli).await);
}

async fn run(cli: Cli) -> i32 {
    let provider_config = match resolve_provider_config(&cli.config, process_environment) {
        Ok(config) => config,
        Err(error) => {
            eprintln!("error: {error}");
            return 2;
        }
    };

    let Cli {
        command,
        print: print_task,
        output_format,
        allow_shell,
        read_only,
        ..
    } = cli;

    match (command, print_task) {
        (Some(Commands::Acp), Some(_)) => {
            eprintln!("error: -p cannot be combined with the acp subcommand");
            2
        }
        (Some(Commands::Acp), None) => run_acp(provider_config).await,
        (None, Some(task)) => {
            run_print(
                provider_config,
                &task,
                output_format,
                allow_shell,
                read_only,
            )
            .await
        }
        (None, None) => match describe_configuration(provider_config) {
            Ok(()) => 0,
            Err(error) => {
                eprintln!("error: {error}");
                1
            }
        },
    }
}

async fn run_acp(provider_config: ApiProviderConfig) -> i32 {
    // Shell is opt-in at the policy layer (`LocalRunnerPolicy::coding` does
    // not include it: it is the tool with no confinement, the one place the
    // permission gate is the only boundary). ACP is exactly the surface
    // built to gate it -- the client asks the user before every call
    // (`acp/permission.rs`'s default policy always routes `shell` through
    // `session/request_permission`) -- so a coding agent that can never
    // build, test, or run `git` would not be a meaningfully useful trade
    // for that safety.
    let tool_policy = LocalRunnerPolicy::coding().with_tool(LocalTool::Shell);
    match structure_cli::acp::run(provider_config, tool_policy).await {
        Ok(()) => 0,
        Err(error) => {
            eprintln!("error: {error}");
            1
        }
    }
}

async fn run_print(
    provider_config: ApiProviderConfig,
    task: &str,
    output_format: OutputFormat,
    allow_shell: bool,
    read_only: bool,
) -> i32 {
    let task = match print::resolve_task(task) {
        Ok(task) => task,
        Err(error) => {
            eprintln!("error: reading task: {error}");
            return 2;
        }
    };
    print::run(
        provider_config,
        PrintOptions {
            task,
            output_format,
            allow_shell,
            read_only,
        },
    )
    .await
}

/// No subcommand and no `-p`: report the resolved configuration and exit.
/// Kept for smoke-testing configuration outside of an editor or a task.
fn describe_configuration(
    provider_config: ApiProviderConfig,
) -> Result<(), Box<dyn std::error::Error>> {
    // Never printed: the api_key field itself is not touched below.
    let api_type = provider_config.api_type;
    let base_url = provider_config.base_url.clone();
    let model_name = provider_config.model.clone();

    let model = HostModel::Api(ApiModelProvider::new(provider_config)?);
    let runner_root = std::env::current_dir()?;
    let runtime = build_host_runtime(model, &runner_root, LocalRunnerPolicy::coding());

    println!("structure: configuration resolved, no subcommand implemented yet");
    println!("  api_type:    {api_type}");
    println!("  base_url:    {base_url}");
    println!("  model:       {model_name}");
    println!("  runner_root: {}", runner_root.display());
    println!(
        "  tools:       {}",
        runtime
            .tools()
            .iter()
            .map(|tool| tool.name.as_str())
            .collect::<Vec<_>>()
            .join(", ")
    );
    println!("  next: run `structure acp` (Agent Client Protocol) or `structure -p \"task\"`");
    Ok(())
}
