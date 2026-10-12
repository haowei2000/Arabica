set dotenv-load := true
set dotenv-filename := ".env.local"
set dotenv-required := false
set dotenv-override := true

_default:
    @just --list

dev-init:
    bash scripts/dev-env.sh

check:
    cargo check --workspace

check-pkg package:
    cargo check -p {{quote(package)}}

test-pkg package:
    cargo test -p {{quote(package)}}

lint-pkg package:
    cargo clippy -p {{quote(package)}} --all-targets -- -D warnings

lint:
    cargo clippy --workspace --all-targets -- -D warnings

fmt-check:
    cargo fmt --all --check

build:
    cargo build --workspace

ci: fmt-check check lint
    cargo test --workspace

server:
    @test -f .env.local || { echo 'Run just dev-init first.' >&2; exit 1; }
    cargo run -p arabica-server

chat:
    @test -f .env.local || { echo 'Run just dev-init first.' >&2; exit 1; }
    cargo run -p arabica-cli --bin arabica

desktop-test:
    cd apps/arabica-desktop && swift test

desktop-package:
    cd apps/arabica-desktop && bash scripts/package.sh debug

# Execute the bundle binary directly so ACP inherits this worktree's environment.
desktop-run:
    @test -f .env.local || { echo 'Run just dev-init first.' >&2; exit 1; }
    apps/arabica-desktop/dist/Arabica.app/Contents/MacOS/ArabicaDesktop

inventory:
    git worktree list
    wt list --full
    sccache --show-stats
    df -h /

prune-preview:
    wt step prune --dry-run
