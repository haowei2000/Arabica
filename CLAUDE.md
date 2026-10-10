# Structure Development

Follow AGENTS.md for development rules and README.md for the Rust workspace.
Production code lives in crates/; the single experiment package lives in
benchmarks/runtime-short-memory/. The Python service and experiment runners
have been removed. See benchmarks/README.md for offline validation commands.

For Rust builds across concurrent Git worktrees, follow the build-cache rules
in AGENTS.md: keep each worktree's `target/` independent, use the developer's
global `sccache` via `RUSTC_WRAPPER` for cross-worktree reuse, and avoid copying
or cleaning artifacts during active Cargo commands. `CARGO_INCREMENTAL=0` is
recommended for parallel worktrees. Preserve debug profiles, and preview
cleanup before removing build artifacts. Local sccache capacity settings must
be verified with `sccache --show-stats` because support varies by version.
