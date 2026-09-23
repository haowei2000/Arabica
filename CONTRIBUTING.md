# Contributing to Structure

Structure is currently a backend-only Rust workspace. There is no frontend, and
the former Python implementation and benchmark frameworks have been removed.
Maintain one experiment package: `structure-short-memory-benchmark`.

## Development setup

Install Rust 1.85 or newer, then run:

```bash
cargo build --workspace
cargo test --workspace
```

## Quality checks

Before opening a pull request, run:

```bash
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

## Pull requests

- Keep changes focused and explain observable behavior changes.
- Add tests for protocol, runtime, provider, runner, or session behavior.
- Preserve crate boundaries and canonical command/event types.
- Do not commit credentials, private endpoints, user data, or generated output.
- Use Conventional Commits, such as `fix(session): preserve event sequence`.

Highlight changes involving credentials, tool execution, persistence, protocol
compatibility, or untrusted input in the pull-request description.
