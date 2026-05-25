use anyhow::{anyhow, Context, Result};
use clap::{Args, Parser, Subcommand};
use crossterm::cursor::{Hide, MoveTo, Show};
use crossterm::event::{self, Event, KeyCode, KeyEventKind};
use crossterm::execute;
use crossterm::style::{Attribute, Print, SetAttribute};
use crossterm::terminal::{self, Clear, ClearType, EnterAlternateScreen, LeaveAlternateScreen};
use std::io::{stdout, Write};
use std::path::PathBuf;
use std::time::Duration;
use structure_local_core::{
    collect_snapshot, default_repo_root, read_repo_text_file, recent_benchmark_reports,
    snapshot_json, LocalSnapshot,
};

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

#[derive(Debug, Subcommand)]
enum BenchCommand {
    List(ListBenchArgs),
    Show(ShowBenchArgs),
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
    #[arg(long, default_value_t = 200_000)]
    max_bytes: u64,
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    let repo_root = match cli.repo_root {
        Some(path) => local_result(structure_local_core::find_repo_root(path))?,
        None => local_result(default_repo_root())?,
    };

    match cli.command {
        Command::Status(args) => {
            let snapshot = local_result(collect_snapshot(&repo_root))?;
            if args.json {
                println!("{}", local_result(snapshot_json(&snapshot))?);
            } else {
                print_snapshot(&snapshot);
            }
        }
        Command::Bench { command } => match command {
            BenchCommand::List(args) => {
                let reports = local_result(recent_benchmark_reports(&repo_root, args.limit))?;
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
                let text =
                    local_result(read_repo_text_file(&repo_root, args.path, args.max_bytes))?;
                print!("{text}");
            }
        },
        Command::Tui => run_tui(&repo_root)?,
    }
    Ok(())
}

fn print_snapshot(snapshot: &LocalSnapshot) {
    println!("Structure local");
    println!("  repo:       {}", snapshot.repo_root);
    println!("  runtime:    {}", snapshot.runtime_dir);
    println!("  frontend:   {}", snapshot.frontend_dir);
    println!("  local only: {}", snapshot.local_only);
    println!("  benchmark reports: {}", snapshot.benchmark_report_count);
    if snapshot.recent_benchmark_reports.is_empty() {
        println!("  recent:     none");
    } else {
        println!("  recent:");
        for report in &snapshot.recent_benchmark_reports {
            println!("    {} ({} bytes)", report.relative_path, report.size_bytes);
        }
    }
}

fn run_tui(repo_root: &PathBuf) -> Result<()> {
    terminal::enable_raw_mode().context("failed to enable raw mode")?;
    let mut out = stdout();
    execute!(out, EnterAlternateScreen, Hide).context("failed to enter TUI")?;

    let result = tui_loop(repo_root, &mut out);

    execute!(out, Show, LeaveAlternateScreen).ok();
    terminal::disable_raw_mode().ok();
    result
}

fn tui_loop(repo_root: &PathBuf, out: &mut impl Write) -> Result<()> {
    let mut snapshot = local_result(collect_snapshot(repo_root))?;
    loop {
        draw_tui(out, &snapshot)?;
        if event::poll(Duration::from_millis(500))? {
            match event::read()? {
                Event::Key(key) if key.kind == KeyEventKind::Press => match key.code {
                    KeyCode::Char('q') | KeyCode::Esc => return Ok(()),
                    KeyCode::Char('r') => snapshot = local_result(collect_snapshot(repo_root))?,
                    _ => {}
                },
                _ => {}
            }
        }
    }
}

fn draw_tui(out: &mut impl Write, snapshot: &LocalSnapshot) -> Result<()> {
    execute!(out, Clear(ClearType::All), MoveTo(0, 0))?;
    execute!(
        out,
        SetAttribute(Attribute::Bold),
        Print("Structure Local"),
        SetAttribute(Attribute::Reset),
        Print("  q quit  r refresh\n\n")
    )?;
    execute!(
        out,
        Print("Mode: local only, no API server required\n"),
        Print(format!("Repo: {}\n", snapshot.repo_root)),
        Print(format!("Runtime: {}\n\n", snapshot.runtime_dir)),
        SetAttribute(Attribute::Bold),
        Print("Recent benchmark reports\n"),
        SetAttribute(Attribute::Reset)
    )?;
    if snapshot.recent_benchmark_reports.is_empty() {
        execute!(out, Print("  none\n"))?;
    } else {
        for report in &snapshot.recent_benchmark_reports {
            execute!(
                out,
                Print(format!(
                    "  {:<64} {:>10} bytes\n",
                    truncate(&report.relative_path, 64),
                    report.size_bytes
                ))
            )?;
        }
    }
    out.flush()?;
    Ok(())
}

fn truncate(value: &str, max_chars: usize) -> String {
    if value.chars().count() <= max_chars {
        return value.to_string();
    }
    let mut out = value
        .chars()
        .take(max_chars.saturating_sub(3))
        .collect::<String>();
    out.push_str("...");
    out
}

fn local_result<T>(result: std::result::Result<T, String>) -> Result<T> {
    result.map_err(|message| anyhow!(message))
}
