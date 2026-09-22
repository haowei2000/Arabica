# Structure

Structure is a headless AI-agent runtime written in Rust. The project defines a
canonical command/event protocol, session lifecycle, model-provider boundary,
tool runner, memory policies, and an HTTP/SSE host.

> Status: active development (`0.1.0`). There is no GUI in this repository;
> `structure-cli` (below) is the only client-facing surface.

## Architecture

The Cargo workspace is split into small crates with explicit ownership:

| Crate | Responsibility |
|---|---|
| `structure-model` | Shared model-facing content and tool types |
| `structure-protocol` | Canonical commands, events, identifiers, and schema |
| `structure-provider` | Model-provider abstraction and API implementations |
| `structure-runner` | Local tool execution boundary |
| `structure-runtime` | Agent loop, long memory, and short-memory projection |
| `structure-session` | Session identity, sequencing, history, and lifecycle |
| `structure-server` | Axum HTTP and SSE protocol host |
| `structure-cli` | Stdio composition host: `structure acp` (Agent Client Protocol) and `structure -p` (one-shot) |

The runtime is protocol-first: clients submit a `CommandEnvelope`, the session
layer validates and sequences the operation, and the runtime emits canonical
`EventEnvelope` values. The HTTP host exposes the same contract without adding
client-specific state.

## HTTP API

`structure-server` currently exposes:

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness check |
| `GET /v1/schema` | Canonical protocol schema |
| `POST /v1/commands` | Submit a command and receive emitted events |
| `GET /v1/events` | Subscribe to events over SSE |

The server listens on `127.0.0.1:4096` by default.

## Requirements

- Rust 1.88 or newer
- An OpenAI-compatible model endpoint when running the HTTP server or the CLI

## Build and Test

```bash
cargo build --workspace
cargo test --workspace
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
```

## Run the Server

Configure the model provider:

```bash
export OPENAI__API_KEY="..."
export OPENAI__BASE_URL="https://api.openai.com/v1"
export OPENAI__MODEL="..."
```

Then start the host:

```bash
cargo run -p structure-server
```

Optional configuration:

- `STRUCTURE__PORT`: listening port, default `4096`
- `STRUCTURE__API_TYPE`: provider API dialect, default
  `open_ai_chat_completions`; `open_ai_responses` enables stateless Responses
  replay with exact reasoning/output-item retention
- `STRUCTURE__TOOL_ROOT`: root directory available to the local runner
- `STRUCTURE__COMPACTION_STRATEGY`: `file_backed_gc` (default), `pointer_gc`,
  or `disabled`
- `STRUCTURE__ARCHIVE_ROOT`: local lossless-compaction archive directory, default
  `target/structure-runtime-memory`
- `COMPACTION_EFFORT`: minimum expected return over one cache reset; legacy
  `PGC_EFFORT` remains accepted

The current server and Harbor benchmark entry points use the atomic local-file
archive adapter. SQLite remains available as an explicit Runtime adapter but is
not selected by either executable.

## Run the CLI

`structure-cli` builds a `structure` binary with two entry points: `structure
acp` (Agent Client Protocol v1 over stdio, for editors like Zed) and
`structure -p "task"` (one-shot execution). Both read the same environment
variables as the server above. See [`docs/cli.md`](docs/cli.md) for flags,
exit codes, and a Zed configuration snippet.

```bash
cargo build -p structure-cli --release
./target/release/structure -p "explain this repository's crate layout"
```

## Benchmarks

The Rust short-memory benchmark is a workspace member:

```bash
cargo run -p structure-short-memory-benchmark
```

Benchmark inputs, policies, and evidence gates live under
`benchmarks/runtime-short-memory/`.

## Repository Status

The Python service, memory benchmark framework, and Harbor Python orchestration
have been removed. The Rust workspace is the single maintained implementation;
`structure-short-memory-benchmark` is the single experiment package. JavaScript
fixtures and the CLI proxy remain supporting tools for that package.

Run `make check` for Rust checks and offline benchmark/proxy tests (Node.js is
required for proxy tests). See `benchmarks/README.md` for available entry points.
The old Python CLI and import interfaces are no longer available. The former
Python container deployment workflow was removed; tags do not deploy a service.

The paper, reports and frozen protocols preserve historical evidence and may
refer to removed runners. They do not establish reproducibility on this commit.
Local datasets, experiment outputs and archived binaries remain untouched.

No browser UI, React application, or other frontend is currently maintained.

## License

Licensed under the Apache License 2.0. See `LICENSE` and `NOTICE`.
