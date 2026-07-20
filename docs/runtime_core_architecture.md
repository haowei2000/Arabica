# Runtime Core Architecture (v2 — Rust Core)

**Status:** Accepted — Option A (full Rust core) chosen 2026-07-20.
**Decision:** CLI, desktop app, and web all drive **one Rust runtime core**
that owns command I/O, session management, and context management. The Python
backend is retired at the end of the migration. See Appendix A for the
decision record.

---

## 1. Prior Art and Why v1 Failed

The previous design ("same contract, two implementations") defined a canonical
manifest (`core/structure_core.json`) and implemented it twice: the Python
service backend and a ~10k-line Rust `structure-local-runtime` for CLI/desktop.
A 521-line `parity.rs` existed solely to fight drift between the two. The
parity tax won; the whole surface was trimmed in `570e23`.

**v2 rule: one implementation, one protocol, many thin surfaces.**
v1 failed because Rust *duplicated* the core. v2 uses Rust to *replace* it.

## 2. Reference Designs

### opencode (sst/opencode)

- Strict client/server: one server process owns all state, LLM calls, tool
  execution, session persistence, MCP servers. REST + SSE (`/event` bus);
  every state change is a typed event forwarded to clients.
- TUI, desktop app, web client, VS Code extension are **all just clients**.
- OpenAPI 3.1 spec served by the server; client SDK generated from it.
- Permissions (`/permission`, `/question`) flow through the protocol.

**We take:** the local-server topology (clients auto-spawn and attach), SSE
event bus as the single delivery channel, SDK generation, protocol-level
permissions.

### Codex CLI (openai/codex)

- Cargo workspace layered: entry points (`codex-cli`, `codex-tui`,
  `codex-exec`, `codex-app-server`) → core engine (`codex-core`,
  `codex-protocol`, `codex-config`, `codex-state`) → platform (sandboxing,
  model client, `codex-mcp-server`).
- **Protocol-first:** `codex-protocol` defines `Op` in / `EventMsg` out; the
  core is essentially `submit(Op)` / `next_event()`. Every frontend is a
  binding. The App Server exposes the same core over documented JSON-RPC 2.0.
- OpenAI rewrote the core TS → Rust (~95% of the codebase) behind unchanged
  product surfaces — the existence proof for our own cutover plan (§12).

**We take:** the crate layering almost verbatim, protocol as a versioned
artifact, headless `exec` mode, MCP as the tool boundary, and the lesson that
a core rewrite behind a frozen protocol is survivable.

## 3. Layering and Crate Map

```
┌──────────────────────── surfaces (presentation only) ────────────────────────┐
│   Web (React)        CLI / TUI (ratatui)      App (Tauri)      IDE (later)   │
│       │                     │                     │                          │
│       │              auto-spawn daemon      sidecar daemon                   │
└───────┼─────────────────────┼─────────────────────┼──────────────────────────┘
        │        ONE PROTOCOL: Commands in / Events out (HTTP + SSE)
        ▼                     ▼                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│              structure-server  (axum) — ONE binary, TWO profiles             │
│    serve: multi-tenant, public bind        daemon: single-user, loopback     │
│              also: structure-exec (headless) · MCP server mode               │
├──────────────────────────────────────────────────────────────────────────────┤
│                     structure-core  (headless library)                       │
│   agent loop · run state machine · session lifecycle · event-log semantics   │
│   context layer (path-addressable store + disclosure levels)                 │
│   tool routing (MCP client) · streaming LLM client (OpenAI-compatible)       │
│                          — depends only on ports —                           │
├──────────────────────────────────────────────────────────────────────────────┤
│         service profile                    │        local profile            │
│   Postgres (sqlx) · Redis Streams · S3     │  SQLite (sqlx) · in-proc bus    │
│                                            │  · local fs                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Cargo workspace

```
Cargo.toml                    # workspace root
crates/
  structure-protocol/         # Command/Event serde types, protocol_version,
                              #   error codes, JSON Schema export (schemars)
  structure-core/             # agent loop, sessions, state machine, context
                              #   layer, event-log semantics, ports (traits)
  structure-adapters/         # EventStore/EventBus/BlobStore impls:
                              #   sqlite, postgres, redis-streams, in-proc, fs, s3
  structure-server/           # axum HTTP+SSE binding; --profile local|service
  structure-exec/             # headless one-shot: events as JSONL on stdout
  structure-cli/              # entry point: spawn/attach daemon, subcommands
  structure-tui/              # ratatui client over HTTP+SSE
frontend/src-tauri/           # shell only; sidecars structure-server (local)
```

Dependency rule (enforced with cargo-deny / workspace lints): arrows only
point down. `structure-core` may not depend on axum, sqlx, or redis;
`structure-protocol` depends on serde/schemars only.

### Foundation choices

| Concern | Choice |
|---|---|
| Async runtime / HTTP | tokio + axum + tower |
| Storage | sqlx — same async query layer for SQLite (local) and Postgres (service) |
| Event bus | Redis Streams (service) / tokio broadcast + mpsc (local) |
| LLM client | reqwest, streaming, OpenAI-compatible endpoint abstraction (covers OpenAI, DashScope, Ollama) |
| Tool boundary | MCP via rmcp (client side); existing Python tools wrapped as FastMCP servers |
| Schema / SDK | schemars → JSON Schema; utoipa → OpenAPI from axum; TS SDK generated into `frontend/src/sdk/` |
| Errors / logs | thiserror / tracing |

## 4. The Protocol (`structure-protocol`)

The single most important artifact, now a Rust crate that is the source of
truth for every surface.

### Commands (client → runtime)

| Command | Notes |
|---|---|
| `session.create` / `session.fork` / `session.resume` | workspace + run lifecycle |
| `message.send` | user input into a session |
| `run.cancel` | state machine transition |
| `tool.approval.response` | §9 |
| `context.read` / `context.search` / `context.update` | context ops as first-class protocol |
| `context.set_disclosure` (glance/overview/detail) | the paper's disclosure levels |
| `config.get` / `config.set` | |

### Events (runtime → client)

The Python system's event taxonomy carries over (it is the paper's audit-trail
claim); the old Rust `RunEventKind` enum (33 variants, `5e33a69`) is the seed
vocabulary, reconciled against the Python event types. Events gain
`protocol_version` and one delivery guarantee: **everything a surface needs is
on the stream; no surface polls state the stream already carries.**

### Transport bindings

1. **HTTP + SSE** (primary): REST for commands, `GET /event` for the stream.
   Identical for both profiles — the web frontend cannot tell cloud from
   localhost.
2. **stdio JSON-RPC** (later): codex-app-server pattern for IDE embedding.
   Same types, different framing.

### SDK

axum + utoipa serve the OpenAPI spec; the TypeScript SDK is generated from it
(opencode's approach). No hand-written `api.ts`. Rust clients (tui, cli) link
`structure-protocol` directly.

## 5. The Core (`structure-core`)

Port map from the Python implementation — Python is the behavioral reference
until cutover:

| Component | Python reference | Old Rust salvage (`5e33a69`) |
|---|---|---|
| Agent loop | `plugins/executors/`, `services/executor/runtime.py` | `runtime.rs` — fragments only (sync design, rewrite async) |
| Run state machine | `services/runs/run_state_machine.py` | `types.rs` `RunStatus` (add `waiting_for_tool`, `cancelled`) |
| Event log semantics | `services/events/event_publisher.py` (sequencing per run/workspace) | `RunEventKind` taxonomy |
| Context layer | `core/frameworks/context_layer.py` (path addressing, glob, DetailLevel) | — (port from Python; this is the paper's core claim) |
| Tool calling | `core/tool_calling/` strategies | `tools.rs` — concept only; replaced by MCP routing |
| LLM client | `extensions/llm/` | `model.rs` — trait shape only; rewrite streaming |
| Sessions | workspaces + runs services | `store.rs` schema shape |

### Ports (traits in `structure-core`)

```rust
trait EventStore   // append, read_range, replay
trait EventBus     // publish, subscribe (consumer-group semantics)
trait BlobStore    // files / artifacts
trait ContextStore // persistent workspace context
```

| Port | Service profile | Local profile |
|---|---|---|
| EventStore | Postgres (sqlx) | SQLite (sqlx), `~/.structure/<workspace>.db` |
| EventBus | Redis Streams + consumer groups | tokio broadcast (in-proc) |
| BlobStore | S3 | local fs |
| Execution | horizontal worker processes | in-process task set |

## 6. Hosts

| Host | What it is | Analog |
|---|---|---|
| `structure-server --profile service` | multi-tenant hosted backend, public bind | current FastAPI deployment |
| `structure-server --profile local` ("daemon") | single-user, loopback, token auth, auto-spawned by clients | opencode server on :4096 |
| `structure-exec` | embed core in-process, JSONL events on stdout, exit code from terminal run state | codex-exec |
| MCP server mode | expose runtime as MCP tools | codex-mcp-server |

One binary for both server profiles is the structural advantage neither
reference has: their hosted products do not run the same code as their local
ones. Ours does by construction.

`structure-exec` doubles as the **benchmark adapter** target:
`benchmarks/adapters/structure.py` speaks the protocol, so it can drive the
Python serve today and Rust `exec` the day it exists — the paper pipeline is
decoupled from the rewrite schedule.

## 7. Surfaces

- **Web (React)** — unchanged UI; swaps hand-written services for the
  generated SDK; points at either profile (offline mode for free).
- **CLI/TUI** — ratatui client over HTTP+SSE; auto-spawns the daemon. The old
  `cli.rs` (3.8k lines) is UX reference for subcommands (`inspect`,
  `run status`, proposal diff/apply/rollback).
- **App (Tauri)** — restore the shell from `5e33a69`, delete its embedded
  runtime, sidecar `structure-server --profile local`, load the web UI from
  localhost. A Rust sidecar in a Tauri app is the native case.
- **IDE extension (future)** — stdio JSON-RPC binding.

CLI and app share one daemon ⇒ they share sessions: start a run in the
terminal, watch it stream in the app.

## 8. What Python Survives

| Survivor | Why |
|---|---|
| `plugins/tools/` → FastMCP servers | tool ecosystem stays Python behind the MCP boundary; no FFI |
| `benchmarks/` | already zero-dependency on `src/structure` by design; drives the protocol |
| research / paper scripts | orthogonal to the runtime |

Everything else under `src/structure/` is retired at cutover (§12 phase 6).

## 9. Approvals and Permissions

Both references route permission through the protocol (opencode
`/permission`; codex approval Ops). Required the moment local tools execute on
user machines:

1. Core emits `tool.approval.request` (tool, args, risk class) and parks the
   run in `waiting_for_tool`.
2. Surface renders the prompt; sends `tool.approval.response`.
3. Policy levels (`auto` / `ask-destructive` / `ask-all`) per workspace;
   decisions are events → audit trail is free.

Native sandboxing for local tool exec (seatbelt on macOS, Landlock on Linux)
is a post-cutover milestone — it is one of the reasons Rust was chosen.

## 10. Salvage Audit (`git show 5e33a69:...`)

| Old code | Lines | Verdict |
|---|---|---|
| `structure-local-runtime/types.rs` | 672 | **mine** — event taxonomy, status enums, usage types seed `structure-protocol` |
| `structure-local-runtime/store.rs` | 1,101 | **reference** — SQLite schema shape; reimplement on sqlx (old code is sync rusqlite) |
| `structure-local-runtime/runtime.rs` | 5,906 | **reference** — agent-loop logic; architecture is sync, rewrite async |
| `structure-local-runtime/model.rs` | 1,178 | **reference** — provider trait shape; no streaming, rewrite |
| `structure-local-runtime/tools.rs` | 660 | **discard** — replaced by MCP routing |
| `structure-local-core/` (manifest, parity, snapshot) | ~1,200 | **discard** — parity machinery is what v2 abolishes |
| `structure-local/cli.rs` | 3,804 | **reference** — subcommand UX |
| `frontend/src-tauri/` | ~1,600 | **restore + gut** — keep shell, delete embedded runtime, add sidecar |

## 11. Migration Plan (Rust-first)

Ordered so every phase ships something usable alone; Python keeps serving
production until phase 6.

1. **Workspace bootstrap** — root `Cargo.toml`, crate skeletons, CI (fmt,
   clippy, test), dependency-direction lints. Import salvage as reference
   under `docs/salvage/` notes, not as code.
2. **`structure-protocol`** — Command/Event types reconciled from Python
   `schemas/` + old `RunEventKind`; schemars JSON Schema export; conformance
   fixtures (golden JSON per event kind); TS SDK generation wired.
3. **`structure-core` + local adapters** — sessions, state machine, event
   sequencing, context layer (path addressing + disclosure levels), streaming
   LLM client, MCP tool routing; SQLite EventStore + in-proc bus.
4. **`structure-exec`** — headless one-shot over the local profile; wire
   `benchmarks/adapters/structure.py` to it (paper unblocked end-to-end on
   Rust; until then the adapter drives Python serve).
5. **`structure-server` (daemon) + TUI + Tauri** — axum HTTP+SSE binding,
   loopback + token auth, auto-spawn; ratatui client; Tauri sidecar loading
   the web UI. *Local-first product complete.*
6. **Service profile + cutover** — Postgres/Redis/S3 adapters, sqlx
   migrations, authn/z port from Python, web frontend on the generated SDK;
   run the protocol conformance suite against Python serve and Rust serve
   (dual-run, diff event streams — the benchmarks oracle pattern); migrate
   event-log data; **retire `src/structure/`**.
7. **Post-cutover** — OS sandboxing for local tools, stdio JSON-RPC binding
   for IDE.

## 12. Comparison Snapshot

| | opencode | Codex CLI | Structure v2 |
|---|---|---|---|
| Core language | TypeScript (Bun) | Rust | **Rust** |
| Protocol | REST + SSE, OpenAPI 3.1 | `Op`/`EventMsg`, JSON-RPC app-server | Command/Event, REST + SSE |
| Local topology | server on :4096, clients attach | core embedded in TUI; app-server for others | daemon on loopback, all clients attach |
| Headless | `run` command | `codex-exec` | `structure-exec` |
| Desktop | Electron over same server | app over app-server | Tauri sidecar over daemon |
| Session persistence | server-side storage | `codex-state` threads | event-sourced log (replay/fork built in) |
| Multi-tenant hosted | share server (partial) | Codex cloud (separate) | same binary, service profile |

## 13. Risks

- **Paper timeline.** The rewrite is months. Mitigation: the benchmark
  adapter speaks protocol (phase 4 note) and can run against the Python
  backend the whole time; phases 1–2 are also the adapter's prerequisites.
- **LangChain/LangGraph loss.** The agent loop is custom (executors), so the
  practical loss is provider-SDK convenience; the OpenAI-compatible HTTP
  abstraction covers OpenAI/DashScope/Ollama.
- **Two stacks during migration.** Bounded by the rule that no new features
  land in the Python core after phase 2 — it is a frozen behavioral reference.
- **Hosted data migration.** Event log is append-only, which makes it the
  easy kind of migration (copy + verify sequence integrity); do it during a
  write freeze at phase 6.

## Appendix A — Decision Record: Rust for the Core

Chosen 2026-07-20: **Option A**, after evaluating:

| Topology | Shape | Verdict |
|---|---|---|
| **A. Full rewrite** | core + hosts in Rust, Python retired | **CHOSEN** — multi-month; accepted for single-binary distribution, cold start, native sandboxing, Tauri synergy |
| B. PyO3 hybrid | Rust core lib, Python `serve` embeds via FFI | rejected: tokio↔asyncio friction, two build systems, bidirectional FFI |
| C. Rust local + Python hosted | two implementations, one contract | rejected: **this is v1** (`parity.rs`); already failed |
| D. Python now, Rust later | swap behind frozen protocol later | superseded by A; its safety mechanism (protocol-first + conformance suite) is retained in the plan |

Prerequisites folded into the plan: MCP as the tool boundary (Python tools
survive as FastMCP servers) and the protocol conformance suite as the cutover
gate.

**Rule: Rust may replace the core; it may never duplicate it.**
