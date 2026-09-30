# Arabica

[Website](https://haowei2000.github.io/Arabica/) · [Configuration](https://haowei2000.github.io/Arabica/configuration.html) · [Apache-2.0 license](LICENSE)

<p align="center">
  <img src="static/arabica-icon.svg" width="120" alt="Arabica icon: roasted coffee beans">
</p>

Arabica is a headless AI-agent runtime written in Rust. The project defines a
canonical command/event protocol, session lifecycle, model-provider boundary,
tool runner, memory policies, and an HTTP/SSE host.

> Status: active development (`0.1.1`). A first native macOS client lives in
> [`apps/arabica-desktop`](apps/arabica-desktop/README.md). The Rust runtime
> remains headless.

## Architecture

The Cargo workspace is split into small crates with explicit ownership:

| Crate | Responsibility |
|---|---|
| `arabica-model` | Shared model-facing content and tool types |
| `arabica-protocol` | Canonical commands, events, identifiers, and schema |
| `arabica-provider` | Model-provider abstraction and API implementations |
| `arabica-runner` | Local tool execution boundary |
| `arabica-runtime` | Agent loop, long memory, and short-memory projection |
| `arabica-session` | Session identity, sequencing, history, and lifecycle |
| `arabica-server` | Axum HTTP and SSE protocol host |
| `arabica-cli` | Stdio composition host: `arabica acp` (Agent Client Protocol) and `arabica -p` (one-shot) |

The runtime is protocol-first: clients submit a `CommandEnvelope`, the session
layer validates and sequences the operation, and the runtime emits canonical
`EventEnvelope` values. The HTTP host exposes the same contract without adding
client-specific state.

The macOS desktop client uses SwiftUI and AppKit. It launches the CLI's ACP
agent as a bundled process and presents conversations, live updates, and tool
permission requests. Build instructions are in its
[README](apps/arabica-desktop/README.md).

## HTTP API

`arabica-server` currently exposes:

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness check |
| `GET /v1/schema` | Canonical protocol schema |
| `POST /v1/commands` | Submit a command and receive emitted events |
| `GET /v1/events` | Subscribe to events over SSE |

The server listens on `127.0.0.1:4096` by default.

## Requirements

- Rust 1.88 or newer
- A supported model-provider endpoint when running the HTTP server or the CLI

## Install the CLI

On macOS or Linux, download the latest release with curl. The installer selects
the matching CPU architecture, verifies the published SHA-256 checksum, and
installs `arabica` to `~/.local/bin` by default:

```bash
curl -fsSL https://haowei2000.github.io/Arabica/install.sh -o install-arabica.sh
sh install-arabica.sh
~/.local/bin/arabica --help
```

Set `ARABICA_INSTALL_DIR` to choose another directory or
`ARABICA_VERSION=v0.1.1` to pin a release. Windows ZIP archives are on
[GitHub Releases](https://github.com/haowei2000/Arabica/releases).

With Rust 1.88 or newer, install the published package from crates.io:

```bash
cargo install arabica-cli --locked
arabica --help
```

The package is named `arabica-cli`; its executable is `arabica`. See the
[configuration guide](https://haowei2000.github.io/Arabica/configuration.html)
for provider credentials, precedence, Blend routing, MCP tools, and server
settings.

## Build and Test

```bash
cargo build --workspace
cargo test --workspace
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
```

## Run the Server

Configure named provider and model candidates. Keys stay in the environment:

```bash
export ARABICA_PROVIDER_OPENAI_API_KEY="..."
export ARABICA_PROVIDER_ANTHROPIC_API_KEY="..."
export ARABICA__PROVIDERS_JSON='{"openai":{"api_type":"open_ai_responses","base_url":"https://api.openai.com/v1"},"anthropic":{"api_type":"anthropic_messages","base_url":"https://api.anthropic.com/v1"}}'
export ARABICA__BLEND_MODELS_JSON='{"fast":{"provider":"openai","model_id":"gpt-4.1-mini"},"strong":{"provider":"anthropic","model_id":"claude-sonnet-4-5"}}'
export ARABICA__BLEND_DEFAULT="fast"
```

Then start the host:

```bash
cargo run -p arabica-server
```

### CLI model blend

The CLI reads `~/.arabica/config.toml`. Define reusable provider settings under `[providers.<name>]`; each `[models.<alias>]` selects a provider and model ID. Blend routes continue to refer to aliases. Provider API keys come from the configured `api_key_env`, or by default from `ARABICA_PROVIDER_<NAME>_API_KEY` (provider name uppercased with non-alphanumeric characters replaced by underscores).

```toml
[providers.openai]
api_type = "open_ai_responses"
base_url = "https://api.openai.com/v1"
api_key_env = "ARABICA_PROVIDER_OPENAI_API_KEY"
max_tokens = 8192
thinking = "high"

[providers.anthropic]
api_type = "anthropic_messages"
base_url = "https://api.anthropic.com/v1"
api_key_env = "ARABICA_PROVIDER_ANTHROPIC_API_KEY"
max_tokens = 8192
request_timeout_secs = 180
anthropic_cache_static_prefix = true

[models.fast]
provider = "openai"
model_id = "gpt-4.1-mini"

[models.strong]
provider = "anthropic"
model_id = "claude-sonnet-4-5"

[blend]
default_policy = "coding"

[blend.policies.coding]
version = 1
default_model = "fast"
after_tool_success = "fast"
after_tool_error = "strong"
recovery_after_no_progress_steps = 2
recovery_model = "strong"
minimum_model_dwell_steps = 2
tool_call_capable_models = ["fast", "strong"]
typed_completion_capable_models = ["fast"]

[blend.policies.precise]
version = 1
default_model = "strong"
after_tool_error = "strong"
minimum_model_dwell_steps = 2
```

The CLI validates every provider reference, alias, credential source, and API-specific setting at startup. A TOML `api_key` is accepted as a fallback to the environment variable; a config file containing keys must be owner-only (`chmod 600`). `arabica auth login` remains a saved-key fallback for the default provider. Blend routing is active in interactive chat, `arabica -p`, and ACP sessions. ACP exposes configured policy IDs and model aliases as session selectors. Selecting a policy changes the current session's routing rules; selecting a model changes that policy's session default. For one policy, the earlier flat `[blend]` fields remain accepted.

Optional configuration:

- `ARABICA__PORT`: listening port, default `4096`
- `ARABICA__PROVIDERS_JSON`: server provider definitions, keyed by provider name
- `ARABICA__BLEND_MODELS_JSON`: server alias entries with `provider` and `model_id`
- `ARABICA__BLEND_DEFAULT`: default alias, defaults to the first alias in sorted order
- `ARABICA__BLEND_AFTER_TOOL_SUCCESS`: optional alias for the next call after
  a successful tool result
- `ARABICA__BLEND_AFTER_TOOL_ERROR`: optional alias after a tool error
- `ARABICA__BLEND_RECOVERY_MODEL`: optional alias after repeated no-progress
  steps (threshold defaults to 2 and is set by
  `ARABICA__BLEND_RECOVERY_AFTER_NO_PROGRESS_STEPS`)
- `ARABICA__BLEND_MINIMUM_MODEL_DWELL_STEPS`: minimum consecutive calls to
  keep a selected model before ordinary routing can switch; error and recovery
  routes bypass this hold, default `1`
- `ARABICA__BLEND_TOOL_CALL_MODELS`: comma-separated aliases certified for
  tool calling
- `ARABICA__BLEND_TYPED_COMPLETION_MODELS`: comma-separated aliases certified
  for typed terminal completion
- `ARABICA__BLEND_POLICY_ID` and `ARABICA__BLEND_POLICY_VERSION`: immutable
  policy identity recorded with each route
- `ARABICA__TOOL_ROOT`: root directory available to the local runner
- `ARABICA__COMPACTION_STRATEGY`: `file_backed_gc` (default), `pointer_gc`,
  or `disabled`
- `ARABICA__ARCHIVE_ROOT`: local lossless-compaction archive directory, default
  `target/arabica-runtime-memory`
- `COMPACTION_EFFORT`: minimum expected return over one cache reset; legacy
  `PGC_EFFORT` remains accepted

The current server and Harbor benchmark entry points use the atomic local-file
archive adapter. SQLite remains available as an explicit Runtime adapter but is
not selected by either executable.

## Run the CLI

`arabica-cli` builds a `structure` binary with two entry points: `structure
acp` (Agent Client Protocol v1 over stdio, for editors like Zed) and
`structure -p "task"` (one-shot execution). Both read provider-specific key
environment variables and the model configuration from `~/.arabica/config.toml`.
See [`docs/cli.md`](docs/cli.md) for flags,
exit codes, and a Zed configuration snippet.

```bash
cargo build -p arabica-cli --release
./target/release/arabica -p "explain this repository's crate layout"
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

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and
[NOTICE](NOTICE). Contributions are accepted under the same license unless
explicitly stated otherwise.
