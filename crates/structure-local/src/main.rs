mod cli;
mod text;
mod tui;

fn main() -> anyhow::Result<()> {
    cli::run()
}
