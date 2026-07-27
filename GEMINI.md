# Structure Project Overview

Structure is a backend-only AI-agent runtime implemented in Rust. Its active
architecture is the Cargo workspace documented in `README.md` and `AGENTS.md`.

There is no frontend in the repository. The previous Python service is retired;
new implementation work belongs in the Rust crates.

Use these checks for normal development:

```bash
cargo build --workspace
cargo test --workspace
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
```

Run the HTTP/SSE host with:

```bash
cargo run -p structure-server
```

See `README.md` for configuration and crate responsibilities, and `AGENTS.md`
for repository development rules.
