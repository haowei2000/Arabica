# Structure Development Guide

Structure is a backend-only, headless AI-agent runtime written in Rust. There
is currently no frontend. The legacy Python implementation and experiment
frameworks have been removed. Maintain the Rust workspace and its single
experiment package; do not reintroduce a parallel implementation.

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
- `crates/arabica-session`: session lifecycle, history, and sequencing
- `crates/arabica-adapters`: persistence adapters implementing the session ports (JSONL session store)
- `crates/arabica-cli`: stdio composition host (interactive chat, `acp`, one-shot mode)
- `crates/arabica-server`: HTTP and SSE transport
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
```

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
