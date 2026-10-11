# Structure Development Guide

Structure has a headless AI-agent runtime written in Rust and a native macOS
client in `apps/arabica-desktop`. The client talks to `arabica acp` over stdio;
it does not duplicate the runtime. The legacy Python implementation and
experiment frameworks have been removed. Maintain the Rust workspace and its
single experiment package; do not reintroduce a parallel implementation.

## Active Workspace

- Rust version: 1.88 (matches the workspace `rust-version`)
- Edition: 2024
- Async runtime: Tokio
- HTTP/SSE host: Axum
- Serialization and schema: Serde and Schemars
- License: Apache-2.0
- Unsafe Rust is forbidden workspace-wide.

### Crate ownership

- `crates/arabica-model`: shared content, message, and tool types
- `crates/arabica-protocol`: canonical commands, events, IDs, and schema
- `crates/arabica-provider`: model-provider boundary
- `crates/arabica-runner`: tool execution boundary
- `crates/arabica-runtime`: orchestration and memory policies
- `crates/arabica-session`: session lifecycle, history, sequencing, and persistence ports
- `crates/arabica-adapters`: persistence adapters implementing the session ports (SQLite session store with legacy JSONL import)
- `crates/arabica-cli`: stdio composition host (interactive chat, `acp`, one-shot mode)
- `crates/arabica-server`: HTTP and SSE transport
- `apps/arabica-desktop`: SwiftUI/AppKit macOS client and ACP process host (outside the Cargo workspace)
- `benchmarks/runtime-short-memory`: active short-memory benchmark (workspace member; `benchmarks/cli-comparison` and `benchmarks/protocol` hold protocol documents and fixtures only)

Dependency direction should follow these boundaries. Transport concerns belong
in `arabica-server`; protocol types belong in `arabica-protocol`; session
sequencing does not belong in the runtime.

## Commands

```bash
cargo build --workspace
cargo test --workspace
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo run -p arabica-server
cd apps/arabica-desktop && swift test && bash scripts/package.sh debug
```

## Rust Build Cache and Worktrees

- Keep Cargo's `target/` directory local to each checkout/worktree. Never set
  one shared `CARGO_TARGET_DIR` for concurrently active worktrees or agents;
  Cargo build locks and overlapping artifacts can serialize builds or cause
  interference.
- Reuse compilation results across worktrees through a user-level `sccache`
  installation configured as `RUSTC_WRAPPER`, rather than sharing `target/`.
  The user's machine configuration is outside this repository; do not commit
  cache paths, credentials, or machine-specific shell settings.
- `CARGO_INCREMENTAL=0` is the recommended setting for parallel, multi-worktree
  development so compiler outputs are more reusable by sccache and incremental
  directories do not keep growing. A developer may enable incremental builds
  for a single worktree when frequent local edits benefit from them.
- Do not change workspace debug profiles just to reduce cache size. Preserve
  full debug information unless the project explicitly chooses another profile;
  profile changes affect debugging and cache compatibility across the team.
- Do not copy or clean `target/` while a Cargo build, test, clippy, or rustdoc
  command is active in that checkout. Prefer `cargo clean` for deliberate
  cleanup, and preview broad cleanup with `cargo clean --dry-run` first.
- Keep the main checkout's build artifacts when they are useful as a source for
  APFS copy-on-write worktree clones. Worktrunk may be configured locally to
  clone ignored `target/` contents into a new worktree; this is an optimization,
  not a correctness guarantee, and clone only from a quiescent source.
- Retire completed worktrees before deleting build artifacts from active ones.
  For inactive worktrees, consider cleaning `target/` only after at least 21
  days without builds and when it exceeds 2 GiB. Treat these as personal
  maintenance defaults, not automatic repository policy; inspect and preview
  candidates before deletion.
- A local sccache capacity such as 100 GiB is separate from per-worktree
  `target/` usage. Verify the configured limit with `sccache --show-stats`;
  environment variables alone do not prove that the installed sccache version
  accepted the requested cache directory or limit.

The server requires `ARABICA__PROVIDERS_JSON` and
`ARABICA__BLEND_MODELS_JSON`, plus the configured provider key environment
variables. It listens on port 4096 unless `ARABICA__PORT` is set.

## Coding Rules

- Keep code, comments, identifiers, and documentation in English.
- Prefer explicit typed protocol variants over unstructured payloads.
- Preserve deterministic event ordering and per-session sequencing.
- Keep model providers and tool runners behind their existing traits.
- Do not add UI or frontend dependencies unless the product scope changes.
- Keep experiments in the existing Rust benchmark package.
- Add focused unit or conformance tests for behavior changes.
- Run formatting, Clippy, and relevant tests before handoff.

## Security

- Never commit API keys, credentials, private endpoints, or user data.
- Treat tool execution roots and command inputs as untrusted boundaries.
- Avoid logging secrets or full provider authorization headers.
- Call out changes involving credentials, execution, persistence, or protocol
  compatibility during review.

## Commits

Use Conventional Commits, for example:

```text
feat(runtime): add bounded short-memory projection
fix(protocol): reject mismatched run identifiers
docs: describe the headless Rust architecture
```
