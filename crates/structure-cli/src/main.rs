//! `structure`: the CLI binary. `structure acp` speaks Agent Client Protocol
//! v1 over stdio; `structure -p` (one-shot execution) lands in a later
//! commit (see `docs/runtime_core_architecture.md` §11).

use clap::{Parser, Subcommand};
use structure_cli::host::{
    HostConfigArgs, HostModel, LocalRunnerPolicy, build_host_runtime, process_environment,
    resolve_provider_config,
};
use structure_provider::ApiModelProvider;
use structure_runner::LocalTool;

#[derive(Parser, Debug)]
#[command(name = "structure", about = "Structure coding agent host")]
struct Cli {
    #[command(flatten)]
    config: HostConfigArgs,
    #[command(subcommand)]
    command: Option<Commands>,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Run as an Agent Client Protocol v1 agent over stdio (for editors like Zed).
    Acp,
}

#[tokio::main]
async fn main() {
    let cli = Cli::parse();
    if let Err(error) = run(cli).await {
        eprintln!("error: {error}");
        std::process::exit(2);
    }
}

async fn run(cli: Cli) -> Result<(), Box<dyn std::error::Error>> {
    let provider_config = resolve_provider_config(&cli.config, process_environment)?;

    match cli.command {
        Some(Commands::Acp) => {
            // Shell is opt-in at the policy layer (`LocalRunnerPolicy::coding`
            // does not include it: it is the tool with no confinement, the
            // one place the permission gate is the only boundary). ACP is
            // exactly the surface built to gate it -- the client asks the
            // user before every call (`acp/permission.rs`'s default policy
            // always routes `shell` through `session/request_permission`) --
            // so a coding agent that can never build, test, or run `git`
            // would not be a meaningfully useful trade for that safety.
            let tool_policy = LocalRunnerPolicy::coding().with_tool(LocalTool::Shell);
            structure_cli::acp::run(provider_config, tool_policy).await?;
            Ok(())
        }
        None => describe_configuration(provider_config),
    }
}

/// No subcommand: report the resolved configuration and exit. Kept for
/// smoke-testing configuration outside of an editor; `structure -p` will
/// replace this as the CLI's own default action.
fn describe_configuration(
    provider_config: structure_provider::ApiProviderConfig,
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
    println!(
        "  next: run `structure acp` (Agent Client Protocol) or wait for `structure -p` (one-shot)"
    );
    Ok(())
}
