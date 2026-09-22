//! `structure`: the CLI binary. Today this wires configuration and the
//! runtime and reports what it resolved; `structure acp` and `structure -p`
//! land in later commits (see `docs/runtime_core_architecture.md` §11).

use clap::Parser;
use structure_cli::host::{
    HostConfigArgs, HostModel, LocalRunnerPolicy, build_host_runtime, process_environment,
    resolve_provider_config,
};
use structure_provider::ApiModelProvider;

#[derive(Parser, Debug)]
#[command(name = "structure", about = "Structure coding agent host")]
struct Cli {
    #[command(flatten)]
    config: HostConfigArgs,
}

fn main() {
    let cli = Cli::parse();
    if let Err(error) = run(cli) {
        eprintln!("error: {error}");
        std::process::exit(2);
    }
}

fn run(cli: Cli) -> Result<(), Box<dyn std::error::Error>> {
    let provider_config = resolve_provider_config(&cli.config, process_environment)?;
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
        "  next: `structure acp` (Agent Client Protocol) and `structure -p` (one-shot) are not implemented yet"
    );
    Ok(())
}
