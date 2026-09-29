//! `arabica`: interactive terminal host, ACP agent, and one-shot runner.

use arabica_cli::auth::AuthAction;
use arabica_cli::config::{ResolvedCliConfig, resolve_cli_runtime_config};
use arabica_cli::host::{
    HostConfigArgs, LocalRunnerPolicy, build_host_runtime, process_environment,
};
use arabica_cli::interactive::{self, InteractiveOptions};
use arabica_cli::print::{self, OutputFormat, PrintOptions, Resume};
use arabica_cli::sessions::SessionsAction;
use arabica_runner::LocalTool;
use clap::{Args, Parser, Subcommand};
use std::io::IsTerminal;

#[derive(Parser, Debug)]
#[command(name = "arabica", about = "Arabica coding agent host")]
struct Cli {
    #[command(flatten)]
    config: HostConfigArgs,
    #[command(subcommand)]
    command: Option<Commands>,
    #[command(flatten)]
    print_args: PrintArgs,
}

#[derive(Args, Debug)]
struct PrintArgs {
    /// Run one task non-interactively and exit. Pass "-" to read the task
    /// from stdin instead of the argument.
    #[arg(short = 'p', long = "print", value_name = "TASK")]
    print: Option<String>,
    /// Output format for -p: "text" (default; stdout is only the final
    /// answer) or "jsonl" (stdout is one JSON event per line, live).
    #[arg(long, value_enum, default_value_t = OutputFormat::Text)]
    output_format: OutputFormat,
    /// Allow terminal chat or -p to run the shell tool. Off by default.
    /// Interactive chat asks before execution; -p runs it outright.
    #[arg(long)]
    allow_shell: bool,
    /// Restrict terminal chat or -p to read-only tools.
    #[arg(long)]
    read_only: bool,
    /// Use the simple line-based terminal instead of the full-screen TUI.
    #[arg(long)]
    plain: bool,
    /// Continue the most recent session in this workspace instead
    /// of starting a new one. Mutually exclusive with --resume.
    #[arg(long = "continue", conflicts_with = "resume")]
    resume_last: bool,
    /// Resume a specific session by id instead of starting a new one.
    /// Mutually exclusive with --continue. See `arabica sessions list`.
    #[arg(long, value_name = "ID", conflicts_with = "resume_last")]
    resume: Option<String>,
}

impl PrintArgs {
    fn resume_mode(&self) -> Resume {
        if self.resume_last {
            Resume::Continue
        } else if let Some(id) = &self.resume {
            Resume::Id(id.clone())
        } else {
            Resume::None
        }
    }
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Run as an Agent Client Protocol v1 agent over stdio (for editors like Zed).
    Acp,
    /// Start an interactive terminal session (also the default).
    Chat,
    /// Show the resolved provider configuration without starting a session.
    Config,
    /// Manage the terminal CLI's saved API key.
    Auth {
        #[command(subcommand)]
        action: AuthAction,
    },
    /// Inspect sessions stored under $ARABICA_HOME.
    Sessions {
        #[command(subcommand)]
        action: SessionsAction,
    },
}

#[tokio::main]
async fn main() {
    let cli = Cli::parse();
    std::process::exit(run(cli).await);
}

async fn run(cli: Cli) -> i32 {
    let Cli {
        config,
        command,
        print_args,
    } = cli;

    // These commands need no working model provider.
    if let Some(Commands::Sessions { action }) = command {
        return if print_args.print.is_some() {
            eprintln!("error: -p cannot be combined with the sessions subcommand");
            2
        } else {
            arabica_cli::sessions::run(action)
        };
    }
    if let Some(Commands::Auth { action }) = command {
        return if print_args.print.is_some() {
            eprintln!("error: -p cannot be combined with the auth subcommand");
            2
        } else {
            arabica_cli::auth::run(action)
        };
    }

    let resolved = match (|| {
        let home = arabica_adapters::default_arabica_home()?;
        let cwd = std::env::current_dir()?;
        resolve_cli_runtime_config(&config, &home, &cwd, process_environment)
    })() {
        Ok(config) => config,
        Err(error) => {
            eprintln!("error: {error}");
            return 2;
        }
    };

    match (command, &print_args.print) {
        (Some(Commands::Acp), Some(_)) => {
            eprintln!("error: -p cannot be combined with the acp subcommand");
            2
        }
        (Some(Commands::Acp), None) => run_acp(resolved).await,
        (Some(Commands::Chat | Commands::Config), Some(_)) => {
            eprintln!("error: -p cannot be combined with this subcommand");
            2
        }
        (Some(Commands::Chat), None) => run_chat(resolved, print_args).await,
        (Some(Commands::Config), None) => match describe_configuration(resolved) {
            Ok(()) => 0,
            Err(error) => {
                eprintln!("error: {error}");
                1
            }
        },
        (Some(Commands::Sessions { .. }), _) => {
            unreachable!("Commands::Sessions returns early above")
        }
        (Some(Commands::Auth { .. }), _) => {
            unreachable!("Commands::Auth returns early above")
        }
        (None, Some(task)) => run_print(resolved, task.clone(), print_args).await,
        (None, None) => run_chat(resolved, print_args).await,
    }
}

async fn run_chat(resolved: ResolvedCliConfig, args: PrintArgs) -> i32 {
    if args.output_format != OutputFormat::Text {
        eprintln!("error: --output-format is only available with -p");
        return 2;
    }
    let options = InteractiveOptions {
        allow_shell: args.allow_shell,
        read_only: args.read_only,
        resume: args.resume_mode(),
    };
    if !args.plain && std::io::stdin().is_terminal() && std::io::stdout().is_terminal() {
        match resolved.build_model() {
            Ok(model) => {
                arabica_cli::tui::run_with_model(
                    resolved.provider,
                    model,
                    resolved.blend_policy,
                    options,
                )
                .await
            }
            Err(error) => {
                eprintln!("error: {error}");
                2
            }
        }
    } else {
        match resolved.build_model() {
            Ok(model) => {
                interactive::run_with_model(
                    resolved.provider,
                    model,
                    resolved.blend_policy,
                    options,
                )
                .await
            }
            Err(error) => {
                eprintln!("error: {error}");
                2
            }
        }
    }
}

async fn run_acp(resolved: ResolvedCliConfig) -> i32 {
    // Shell is opt-in at the policy layer (`LocalRunnerPolicy::coding` does
    // not include it: it is the tool with no confinement, the one place the
    // permission gate is the only boundary). ACP is exactly the surface
    // built to gate it -- the client asks the user before every call
    // (`acp/permission.rs`'s default policy always routes `shell` through
    // `session/request_permission`) -- so a coding agent that can never
    // build, test, or run `git` would not be a meaningfully useful trade
    // for that safety.
    let tool_policy = LocalRunnerPolicy::coding().with_tool(LocalTool::Shell);
    match arabica_cli::acp::run(resolved, tool_policy).await {
        Ok(()) => 0,
        Err(error) => {
            eprintln!("error: {error}");
            1
        }
    }
}

async fn run_print(resolved: ResolvedCliConfig, task: String, args: PrintArgs) -> i32 {
    let task = match print::resolve_task(&task) {
        Ok(task) => task,
        Err(error) => {
            eprintln!("error: reading task: {error}");
            return 2;
        }
    };
    let resume = args.resume_mode();
    let model = match resolved.build_model() {
        Ok(model) => model,
        Err(error) => {
            eprintln!("error: {error}");
            return 2;
        }
    };
    print::run_with_model(
        model,
        resolved.blend_policy,
        PrintOptions {
            task,
            output_format: args.output_format,
            allow_shell: args.allow_shell,
            read_only: args.read_only,
            resume,
        },
    )
    .await
}

/// `arabica config`: report the resolved provider without starting a run.
fn describe_configuration(resolved: ResolvedCliConfig) -> Result<(), Box<dyn std::error::Error>> {
    let model = resolved.build_model()?;
    let aliases = resolved.models.clone();
    let blend_policy = resolved.blend_policy.clone();
    let provider_config = resolved.provider;
    // Never printed: the api_key field itself is not touched below.
    let api_type = provider_config.api_type;
    let base_url = provider_config.base_url.clone();
    let model_name = provider_config.model.clone();
    let thinking = if !provider_config.thinking_enabled {
        "off".to_owned()
    } else {
        provider_config
            .reasoning_effort
            .clone()
            .unwrap_or_else(|| "on".to_owned())
    };

    let runner_root = std::env::current_dir()?;
    let arabica_home = arabica_adapters::default_arabica_home()?;
    let runtime = build_host_runtime(
        model,
        &runner_root,
        LocalRunnerPolicy::coding(),
        &arabica_home,
    );

    println!("structure: configuration resolved");
    println!("  api_type:    {api_type}");
    println!("  base_url:    {base_url}");
    println!("  model:       {model_name}");
    if !aliases.is_empty() {
        println!("  models:");
        for (alias, model_id) in aliases {
            println!("    {alias}: {model_id}");
        }
        if let Some(policy) = blend_policy {
            println!(
                "  blend:       {} v{} (default {})",
                policy.policy_id, policy.version, policy.default_model
            );
        }
    }
    println!("  thinking:    {thinking}");
    println!("  runner_root: {}", runner_root.display());
    let home = arabica_adapters::default_arabica_home()?;
    println!(
        "  user config: {}",
        arabica_cli::config::user_config_path(&home).display()
    );
    println!(
        "  workspace:   {}",
        arabica_cli::config::workspace_config_path(&home, &runner_root).display()
    );
    println!(
        "  tools:       {}",
        runtime
            .tools()
            .iter()
            .map(|tool| tool.name.as_str())
            .collect::<Vec<_>>()
            .join(", ")
    );
    println!("  next: run `arabica` for interactive chat or `arabica -p \"task\"` for scripting");
    Ok(())
}
