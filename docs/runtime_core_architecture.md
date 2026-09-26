# Runtime Core Architecture (v2 — Rust Core)

> Historical document: Python services, memory benchmark runners and Harbor
> orchestration were removed at the Rust-only cutover. References to those
> components describe the original design or campaign, not current runnable
> interfaces. See the repository benchmark guide for maintained entry points.

**Status:** Accepted — Option A (full Rust implementation) chosen 2026-07-20;
modular implementation started 2026-07-25.
**Decision:** CLI, desktop app, and web all use **one Command/Event Protocol**
implemented by four explicit modules: Runtime, Session Management, Runner
Environment, and UI. The modules may share a process, but may not bypass the
protocol or absorb each other's responsibilities. The Python backend is
retired at the end of the migration. See Appendix A for the decision record.

---

## 1. Prior Art and Why v1 Failed

The previous design ("same contract, two implementations") defined a canonical
manifest (`core/structure_core.json`) and implemented it twice: the Python
service backend and a ~10k-line Rust `structure-local-runtime` for CLI/desktop.
A 521-line `parity.rs` existed solely to fight drift between the two. The
parity tax won; the whole surface was trimmed in `570ef23`.

**v2 rule: one protocol, explicit module ownership, many thin surfaces.**
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
┌──────────────────────────── UI / surfaces ───────────────────────────────────┐
│   Web (React)       CLI / TUI (ratatui)       App (Tauri)      IDE (later)   │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ Commands in / Events out
                                ▼
┌──────────────────────── Session Management ─────────────────────────────────┐
│ session lifecycle · run scheduling · event sequencing · persistence ports   │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ scheduled command / canonical events
                                ▼
┌────────────────────────────── Runtime ───────────────────────────────────────┐
│ short-memory projection · long-memory access · agent loop · output normalize │
└──────────────────────┬────────────────────────────┬───────────────────────────┘
                       │ model request/response     │ typed tool call/result
                       ▼                            ▼
┌──────────────── Model Provider ─────────┐  ┌──────── Runner Environment ─────┐
│ API codecs · HTTP · streaming deltas    │  │ local/container/remote execution │
│ OpenAI · Anthropic · Gemini adapters    │  │ sandbox · cancel · resource limits│
└─────────────────────────────────────────┘  └──────────────────────────────────┘
```

All arrows use types rooted in `arabica-protocol`. A composition host may
wire the modules in-process or expose the same protocol over HTTP + SSE.

### Cargo workspace

```
Cargo.toml                    # workspace root
crates/
  arabica-model/            # provider-neutral RuntimeItem, RuntimeRequest,
                              #   tools, usage, and stream delta vocabulary
  arabica-provider/         # ModelProvider port, ApiType dispatch, complete
                              #   provider request/response codecs and clients
  arabica-protocol/         # Command/Event serde types, protocol_version,
                              #   error codes; schema export is the next step
  arabica-runner/           # RunnerEnvironment boundary; local/container/
                              #   remote execution implementations
  arabica-runtime/          # context, agent loop, tool routing, normalized
                              #   command output; no session scheduling
  arabica-session/          # session lifecycle, run scheduling, state machine,
                              #   event sequencing, persistence ports
  arabica-adapters/         # EventStore/EventBus/BlobStore impls:
                              #   sqlite, postgres, redis-streams, in-proc, fs, s3
  arabica-server/           # axum HTTP+SSE binding; --profile local|service
  structure-exec/             # headless one-shot: events as JSONL on stdout
```

Dependency rule: `arabica-protocol` is the only shared external wire
vocabulary. `arabica-provider` depends on model + protocol;
`arabica-runner` depends on model + protocol; neither depends on the other.
`arabica-runtime` depends on provider + runner + protocol, and
`arabica-session` depends on protocol + runtime. Composition hosts may
depend on all modules. Runtime and session may not depend on UI, axum, sqlx,
or redis directly.

### Foundation choices

| Concern | Choice |
|---|---|
| Async runtime / HTTP | tokio + axum + tower |
| Storage | sqlx — same async query layer for SQLite (local) and Postgres (service) |
| Event bus | Redis Streams (service) / tokio broadcast + mpsc (local) |
| LLM client | reqwest, streaming, OpenAI-compatible endpoint abstraction (covers OpenAI, DashScope, Ollama) |
| Tool boundary | MCP via rmcp (client side); existing Python tools wrapped as FastMCP servers |
| Schema / SDK | schemars → JSON Schema; utoipa → OpenAPI; surface SDKs generated only after protocol freeze |
| Errors / logs | thiserror / tracing |

## 4. The Protocol (`arabica-protocol`)

The single most important artifact, now a Rust crate that is the source of
truth for every surface. The normative contract, invariants, and freeze
checklist are maintained in [`docs/protocol.md`](protocol.md).

### Commands (client → runtime)

| Command | Notes |
|---|---|
| `session.create` / `session.fork` / `session.resume` | workspace + run lifecycle |
| `message.send` | user input into a session |
| `run.cancel` | state machine transition |
| `tool.approval.response` | §9 — deferred; approvals resolve through a Runtime approver channel and are recorded as `tool.call.permission_*` Events |
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
`arabica-protocol` directly.

## 5. Runtime, Session Management, and Runner Environment

The former `structure-core` responsibility is deliberately split. The
ownership test is simple:

| Question | Owner |
|---|---|
| What context is visible and how is a command interpreted? | Runtime |
| Which session/run executes next and what sequence number is emitted? | Session Management |
| Where and under which resource policy does execution occur? | Runner Environment |
| How is state rendered or user input collected? | UI |

The initial vertical slice lives in `crates/structure-{model,provider,protocol,
runner,runtime,session,server}`. It exercises session creation/fork/close, run scheduling,
validated path-addressable context, progressive disclosure, resolved Context
delivery to the Model Provider, explicit short/long memory separation,
faithful ordered stdout/stderr normalization, runner-failure normalization,
ordered event envelopes, and real OpenAI-compatible Chat Completions execution.
The current OpenAI adapter maps Structure's provider-neutral `ModelRunRequest`
into provider messages: long memory becomes a delimited data-only context,
short-memory conversation facts retain their original user/assistant roles,
the current input precedes active-run memory, and the newest provider/tool step
remains a lossless continuation tail. The typed multi-step tool loop is
implemented; durable adapters, streaming model output, and MCP
routing remain migration work and require their protocol event families to be
frozen first.

Provider dispatch uses `ApiType`, which identifies an API wire dialect rather
than a vendor: `open_ai_chat_completions`, `open_ai_responses`,
`anthropic_messages`, `gemini_generate_content`, or `gemini_interactions`.
`ApiModelProvider` is the single enum-dispatch boundary used by the composition host;
unimplemented dialects fail during startup instead of silently falling back.
`ApiType` must not become the runtime content model. Typed `Message`,
`ToolCall`, `ToolResult`, and streaming delta items remain provider-neutral
Runtime types and are encoded/decoded only inside the selected adapter.

The adapter boundary operates on a complete model turn rather than converting
individual messages in isolation:

```text
RuntimeRequest
  items: RuntimeItem[]
  tools: ToolDefinition[]
  tool_choice: ToolChoice
          │
          ▼ ApiCodec::encode / ApiCodec::decode
Provider wire request/response
          │
          ▼
RuntimeResponse
  items: RuntimeItem[]
  finish_reason
  usage
```

Tool schemas are request-level Structure values. Each codec owns their wire
placement: OpenAI Chat uses top-level `tools[].function`, Anthropic Messages
uses top-level `tools[].input_schema`, Gemini GenerateContent uses
`tools[].function_declarations`, and Gemini Interactions uses typed tool/step
objects. Provider-specific continuation data such as Anthropic signatures,
Gemini thought signatures, or OpenAI encrypted reasoning is retained in typed
`ProviderState`. Cross-dialect conversion must reject state it cannot express;
silent loss is prohibited.

The Runtime kernel is complete for the currently frozen
session/message/context slice. Runtime emits through a Session-owned event-log
sink: every user, tool, output, and terminal event receives its canonical
identity and sequence immediately, and the active run can reproject that same
history before its next model step. The provider/tool loop is bounded at 32
model steps per run, which permits long single-message tasks without allowing
an unbounded agent loop. This does not make the local profile
complete: `SessionManager` and the development HTTP host still await Runner
completion inside command submission. Concurrent cancellation and live chunk
delivery require the planned `CommandReceipt` plus background dispatch work in
Session Management; they must not be simulated inside Runtime.

Short-memory projection is a deterministic materialisation pipeline rather
than transcript filtering. Runtime maps detailed Protocol events into five
retention classes (`Anchor`, `Working`, `Recovery`, `Transient`, `Control`),
then applies event-count TTL with relation-aware decay and pinning. Completion
decay is scoped by stable relation keys such as tool call IDs. Runtime
protects a configurable recency floor, groups related events into
stable `turn`, `tool`, `context`, `transient`, and `misc` batches, and assigns
`LOAD_ALL`, `LOAD_KEY`, or `NO_LOAD`. The result includes explainable
per-event decisions and batch metadata so model input can be reconstructed
from the immutable Session event log and the policy. These load states remain
the pure-policy and benchmark vocabulary. `FileBackedGC` is a separate,
lossless compact strategy and disables BatchKey materialisation in the active
model-step projection. TTL expiry creates eligibility but does not delete an
event. Only a fully TTL-expired, relation-closed batch outside the protected
working tail may enter a compact checkpoint. Its exact canonical envelopes are
serialized to the runtime file archive and SHA-256-verified before they leave
the prompt. Only complete epochs are replaced by typed `MemoryPointer` values,
while the current epoch remains append-only and lossless. Failed writes or
integrity mismatches fail projection instead of losing evidence. The default
is eight eligible batches per checkpoint; benchmarks may use a smaller interval to exercise the transition
in a bounded run. Checkpoint completion is necessary but not sufficient for
collection. Runtime converts removable provider-visible bytes to estimated
tokens using the preceding real request's observed token density. When real
Provider usage is available, the cache-reset cost is the preceding response's
`cached_input_tokens`; if usage is unavailable, Runtime falls back to the
projected stable-prefix token count. It then weights the remaining model step
budget with a geometric survival curve rather than treating checkpoint batch
count as the future reuse horizon. Collection occurs only when
`estimated_saved_tokens_per_call * probability_weighted_remaining_steps >=
estimated_cache_reset_tokens * COMPACTION_EFFORT`. The default continuation
probability is 7,500 basis points and the default effort is 1; higher effort
requires proportionally more expected return. Once an epoch has been archived,
its pointers remain committed even if later observations would reject a new
rewrite, preventing full/pointer oscillation. The server selects
`file_backed_gc` by default through `ARABICA__COMPACTION_STRATEGY`, reads
`COMPACTION_EFFORT` (with `PGC_EFFORT` as a compatibility fallback), and reads
`PGC_CONTINUATION_PROBABILITY_BPS`; embedders configure the same policy through
`CoreRuntime` setters. Each eligible decision is exposed as a
`PointerGcAdmissionObservation` for attribution.

The CLI and HTTP host enable asynchronous FileBackedGC with a file archive.
The foreground model step never writes a newly admitted archive. It projects
from a frozen set of verified archive IDs returned by completed background
workers, and keeps other batches in full context. A worker uses an immutable
event-log snapshot, applies the same profitability gate, persists exact
archives, and publishes its ready IDs only after the whole pass succeeds.
Workers are limited to one per workspace; failed passes leave the current
conversation usable and are exposed as diagnostics. A later pass can verify
and adopt archives left by a prior process or an interrupted pass. The
host does not wait for unfinished workers when a one-shot process exits;
the exact session log remains the recovery source. The
provider-free benchmark keeps synchronous projection for deterministic
comparison. Projection and ready-set checks still run before model calls;
the expensive archive verification and writes run off the request path.

During an active run, the newest tool step is explicitly protected and sent
through the provider's lossless continuation channel. Before every later model
step, older closed tool batches from that same run pass through the normal TTL
and `LOAD_ALL` / `LOAD_KEY` / `NO_LOAD` pipeline. Projected active-run entries
are encoded after the current user input and before the protected continuation,
preserving tool-call/result pairing while bounding older same-run context.
Pointers expose only stable metadata, the verified content hash, and a relative
content-addressed path such as `m/tool/write_file/<sha256>.json` inside Runtime. They are omitted from the
stable prefix and appended as a compact Provider-visible recovery suffix. The
fixed `memory_search` tool discovers matching logical paths without eagerly
loading content, and `memory_read` verifies the archived
SHA-256 digest and hydrates the exact event envelopes through a normal paired
tool call/result continuation. Both tool definitions are stable from the first
model request. The meaningful directory portion is derived from typed event
kind and tool name, while the hash filename prevents collisions without placing
user payloads in paths.

Historical port map from the retired Python implementation. It is provenance
only; new production behavior is defined and tested in the Rust crates above:

| Component | New owner | Python reference | Old Rust salvage (`5e33a69`) |
|---|---|---|---|
| Agent loop | Runtime | `plugins/executors/`, `services/executor/runtime.py` | `runtime.rs` — fragments only (sync design, rewrite async) |
| Run state machine | Session | `services/runs/run_state_machine.py` | `types.rs` `RunStatus` |
| Event sequencing | Session | `services/events/event_publisher.py` | `RunEventKind` taxonomy |
| Context layer | Runtime | `core/frameworks/context_layer.py` | port from Python |
| Tool calling / LLM | Runtime | `core/tool_calling/`, `extensions/llm/` | concepts only; rewrite streaming |
| Process/container execution | Runner | worker and execution tools | replace with explicit runner adapters |
| Session persistence | Session | workspaces + runs services | `store.rs` schema shape |

### Persistence and infrastructure ports

```rust
trait EventStore   // append, read_range, replay
trait EventBus     // publish, subscribe (consumer-group semantics)
trait BlobStore    // files / artifacts
trait LongMemoryStore // persistent workspace context
trait RunnerEnvironment // run, stream output, cancel
```

### Memory boundary

```text
Session Event Log ── pure ShortMemoryProjector ──> load-state diagnostics
        │
        └── checkpoint archival ──> internal MemoryPointer ──> provider-silent

Workspace LongMemoryStore ── disclosure/query ──> ModelRunRequest.long_memory
Runtime evidence archive ── memory_search/read + SHA-256 verify ──> continuation
```

- Short Memory is an ephemeral prompt view. It has no independent store and
  cannot be mutated directly.
- Long Memory is Workspace-scoped, path-addressable durable context. Sessions
  in the same Workspace share it; closing or forking a Session does not delete
  or copy it.
- Archived runtime evidence is stored in a separate namespace and is not
  enumerated as ordinary long-context input. Only explicit `memory_search` and
  `memory_read` calls disclose it to the model.
- `LongMemoryStore` has three archive implementations: process-local memory,
  atomic content-addressed files, and SQLite with an idempotent primary-key
  contract. Runtime selects the adapter at construction; Tier-B uses the file
  adapter so pointer evidence survives Runtime recreation.
- The current server and Harbor entry points select the file adapter. The
  server uses `ARABICA__ARCHIVE_ROOT`, defaulting to
  `target/arabica-runtime-memory`; SQLite remains opt-in for later scale and
  indexing experiments.
- Session Management owns the append-only history and fork boundary. Runtime
  owns the pure projection policy and long-memory disclosure policy.

| Port | Service profile | Local profile |
|---|---|---|
| EventStore | Postgres (sqlx) | SQLite (sqlx), `~/.arabica/<workspace>.db` |
| EventBus | Redis Streams + consumer groups | tokio broadcast (in-proc) |
| BlobStore | S3 | local fs |
| Execution | horizontal worker processes | in-process task set |

## 6. Hosts

| Host | What it is | Analog |
|---|---|---|
| `arabica-server --profile service` | multi-tenant hosted backend, public bind | current FastAPI deployment |
| `arabica-server --profile local` ("daemon") | single-user, loopback, token auth, auto-spawned by clients | opencode server on :4096 |
| `structure-exec` | embed core in-process, JSONL events on stdout, exit code from terminal run state | codex-exec |
| `structure acp` | Agent Client Protocol v1 over stdio, one `SessionManager` per ACP session | Zed's own agent servers, `claude-code-acp` |
| MCP server mode | expose runtime as MCP tools | codex-mcp-server |

One binary for both server profiles is the structural advantage neither
reference has: their hosted products do not run the same code as their local
ones. Ours does by construction.

`structure-exec` is implemented as the print mode of `arabica-cli`
(`crates/arabica-cli/src/print.rs`; Appendix B). The Python benchmark
adapter this section used to pair it with was removed with the Python
implementation in `a3823ee`; the maintained experiment package
(`benchmarks/runtime-short-memory`) links the production crates directly
instead of speaking the protocol over a transport.

`structure acp` is implemented in `crates/arabica-cli/src/acp/`. It is the
other binding of `arabica-cli` alongside `structure-exec`; see Appendix B.

## 7. Surfaces (Deferred)

All CLI, web, and desktop implementation code was removed on 2026-07-25 so
surface behavior cannot drive or prematurely freeze the protocol. No *UI
surface* crate may be reintroduced until the protocol checklist in §11 is
complete.

Amended 2026-09-21 (Appendix B): this rule governs UI surfaces, not composition
hosts. A host wires the existing modules in one process and binds the canonical
contract to a transport without adding vocabulary. `arabica-server` does this
for HTTP + SSE; `arabica-cli` does it for stdio (ACP) and one-shot execution.
Hosts may depend on Runtime, Session Management and Runner Environment, exactly
as `arabica-server` already does.

Future surfaces remain thin clients: they may construct commands, subscribe
to events, and render state, but may not import Runtime, Session Management,
or Runner Environment implementation types.

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

Design agreed 2026-09-21 (Appendix B). Implemented at the Runtime layer
(`crates/arabica-runtime/src/control.rs`, wired into the tool-call loop);
host-side wiring (ACP's `session/request_permission`, the print host's static
policy) lands with `arabica-cli` (§11 T6-T8):

1. Runtime emits `tool.call.permission_requested` (call id only — no
   interaction kind, so the event cannot suggest the classifier participates
   in the decision) after `tool.call.classified` and before the Runner
   executes.
2. The host supplies the decision through `RunControl::permissions`, a
   `ToolPermissionGate` carrying a keyed `ToolPermissionPolicy` and an
   `mpsc` approver channel Runtime sends a `PermissionRequest` on, answered
   through a `oneshot` reply — a Runtime-owned channel, not a protocol
   Command: a Command reply cannot be delivered while the Session Manager
   holds its borrow for the whole run. The ACP host forwards the request to
   the editor as `session/request_permission`; the print host answers from a
   static policy.
3. Runtime emits `tool.call.permission_resolved` (outcome, scope, source), so
   the decision is in the audit trail either way. A `Session`-scoped allow is
   derived from that same canonical log (`session_allows` scans prior
   `permission_resolved` events for the tool name) rather than held in host
   memory, so it behaves identically under every host and survives a Session
   restored from disk.
4. Policy is keyed by **tool name**, never by the shell interaction classifier:
   that classifier is a memory-retention heuristic and mislabels commands such
   as `cat x | sh` as inspection.
5. With no approver configured, or a dropped reply, the gate fails closed
   (`ToolPermissionSource::ApproverUnavailable`). Cancelling the run while a
   decision is pending resolves it as `Cancelled` rather than leaving it
   hanging.
6. A default `RunControl` (`permissions: None`) attaches no gate at all, so
   every recorded benchmark campaign's Events are unaffected.

The Command side (`tool.approval.response`) stays deferred until asynchronous
`CommandReceipt` acceptance exists; see the §11 checklist.

Native sandboxing for local tool exec (seatbelt on macOS, Landlock on Linux)
is a post-cutover milestone — it is one of the reasons Rust was chosen.

## 10. Salvage Audit (`git show 5e33a69:...`)

| Old code | Lines | Verdict |
|---|---|---|
| `structure-local-runtime/types.rs` | 672 | **mine** — event taxonomy, status enums, usage types seed `arabica-protocol` |
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

- [x] **Workspace bootstrap and module split** — root `Cargo.toml`; explicit
  model, provider, protocol, runner, runtime, session, and HTTP/SSE host crates;
  fmt, clippy, tests, and enforced dependency direction.
- [ ] **`arabica-protocol` completion** — core session/message/context and
  typed tool-call vocabulary, JSON Schema export, HTTP command submission, and
  SSE delivery are implemented. Python event reconciliation, golden wire
  fixtures, permission events, and generated SDKs remain.
- [ ] **Runtime + Session + Runner local profile** — short/long memory
  separation, the typed multi-step agent loop, real OpenAI-compatible provider
  calls, and confined LocalRunner file execution are implemented. Durable
  sessions, streaming deltas, background dispatch/event sinks, MCP routing,
  approvals, SQLite EventStore, and the in-process bus remain.
- [x] **`structure-exec`** — headless one-shot over the local profile. Implemented
   as the print mode of `arabica-cli` (`structure -p`, `crates/arabica-cli/src/print.rs`),
   emitting canonical event envelopes as JSONL (`--output-format jsonl`) or just
   the final answer (`--output-format text`, the default). The Python benchmark
   adapter this entry used to feed was removed with the Python implementation
   in `a3823ee`.
- [x] **ACP host** — `structure acp` binds the contract to Agent Client
   Protocol v1 over stdio so editors can drive Structure directly
   (`crates/arabica-cli/src/acp/`). v2 stays behind a feature flag while it
   is draft. See Appendix B.
- [ ] **UI surfaces after protocol freeze** — generate clients from the frozen
   schema, then introduce TUI, web, and desktop shells as separate thin
   clients. No surface-specific command or event variants.
- [ ] **Service profile + cutover** — Postgres/Redis/S3 adapters, sqlx
   migrations, authn/z port from Python, web frontend on the generated SDK;
   run the protocol conformance suite against Python serve and Rust serve
   (dual-run, diff event streams — the benchmarks oracle pattern); migrate
   event-log data; **retire `src/structure/`**.
- [ ] **Post-cutover** — OS sandboxing for local tools, stdio JSON-RPC binding
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

## Appendix B — Decision Record: ACP-First Composition Host

**Status:** Accepted 2026-09-21.
**Decision:** add `crates/arabica-cli`, a composition host with two bindings —
`structure acp` (Agent Client Protocol v1 over stdio) and `structure -p`
(one-shot execution, the `structure-exec` role from §11). ACP is the default
integration surface, so editors can drive Structure without a separate adapter.

### Why this is not the surface reintroduction §7 forbids

The host adds no Command or Event vocabulary and renders nothing. It wires the
same modules `arabica-server` already wires and binds the canonical contract
to a different transport. §7 protects the protocol from being shaped by UI
behavior; a host that only translates cannot do that. TUI, web and desktop
remain blocked by the §9 checklist in `protocol.md`.

### Why ACP v1, not v2

v2 is labelled draft, and the upstream migration guide tells implementers to
negotiate per connection, keep v1 working, and gate v2 behind feature flags.
v1-only clients will remain common. v2 also **removed** the client `fs/*` and
`terminal/*` methods in favour of agent-side execution, which is already how
Structure works: tools run in the Runner Environment. Building on the v1 client
filesystem and terminal APIs would therefore have to be undone for v2.

### Consequences accepted with this decision

| Consequence | Why it is unavoidable |
|---|---|
| Cooperative in-flight cancellation in Runtime (implemented: `RunControl`/`RunCancellation`) | ACP `session/cancel` must stop a run that `SessionManager::handle` is executing under one borrow |
| A Session-level event observer (implemented: `SessionEventObserver`, `IdAllocator`) | ACP streams progress; today events are returned only after the whole run finishes |
| A permission gate recorded as typed Events (implemented: `ToolPermissionGate`) | ACP delegates approval to the client; the decision must still reach the audit trail |
| A Session-scoped denial the gate remembers, not only an allow (implemented: `session_decision`, replacing `session_allows`) | ACP's `RejectAlways` permission option must mean what it says: an ACP surface that offered it against a gate that only remembered allows would silently re-ask |
| A provider-valid history projection (implemented: `HistoryProjection::ExactTranscript`, later supplemented by provider-safe Policy projection) | multi-turn chat must keep each completed tool call adjacent to its result; the CLI now filters duplicate `command.output` records from its model-facing Policy view while retaining them in the audit log and archive so FileBackedGC can operate |
| MSRV 1.85 → 1.88, `serde_json/preserve_order` unified workspace-wide (implemented) | required by `agent-client-protocol`; the feature must be explicit so serialized bytes do not depend on the build invocation |
| The ACP surface itself (implemented: `crates/arabica-cli/src/acp/`) | `initialize`/`session/new`/`session/prompt`/`session/cancel`, the Event→`session/update` mapping, stop-reason derivation, and the permission-request bridge, built against `agent-client-protocol` v2.2.0's builder/handler API rather than the simpler trait-based shape earlier drafts of this document assumed -- see that crate's own `concepts::ordering` module for why `session/prompt` must `cx.spawn` its run and return immediately |

### Alternatives rejected

- **A separate adapter process** (the `claude-code-acp` shape) — rejected:
  "ACP by default" means built in, and an external adapter would duplicate the
  event-to-update mapping outside the repository that owns the vocabulary.
- **Hand-rolled JSON-RPC** — rejected: the official `agent-client-protocol`
  crate is Apache-2.0, is what Zed itself uses, and tracks a moving spec.

**Rule: a host may bind the contract; it may never extend it.**
