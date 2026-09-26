# Structure Roadmap

Structure is a backend-only, headless AI-agent runtime. This document describes
the current state of the Rust workspace and the planned evolution order. It is
a living document: update it when a milestone ships or when scope changes, and
reference items in Conventional Commits (for example,
`feat(provider): implement Gemini GenerateContent adapter (roadmap P2)`).

Scope guardrails from `AGENTS.md` remain binding: no frontend/UI work, no
parallel legacy implementations, no `unsafe`, and experiments stay inside the
`benchmarks/` package.

---

## Where we are today

A short inventory of what already exists, so roadmap items are read against
reality rather than aspiration.

**Composition hosts**

- `arabica-server`: Axum HTTP host with `GET /health`, `GET /v1/schema`,
  `POST /v1/commands` (command in, canonical events out), and `GET /v1/events`
  (SSE broadcast fan-out). Configured via `OPENAI__*` / `STRUCTURE__*`
  environment variables.
- `arabica-cli`: stdio composition host with interactive chat, one-shot
  `structure -p`, Agent Client Protocol (`acp`), MCP support, checkpoints,
  session management, and a TUI.

**Model-provider boundary (`arabica-provider`)**

- Implemented API dialects: OpenAI Chat Completions (streaming with
  non-streaming fallback), OpenAI Responses (stateless continuation with
  encrypted reasoning replay), Anthropic Messages (streaming + static-prefix
  prompt caching).
- `ApiType::GeminiGenerateContent` and `ApiType::GeminiInteractions` are
  declared in the wire contract but have **no adapters yet**.
- Raw-exchange capture for reproducibility (request/response bytes, never
  authorization headers), transient-send retries, and explicit experiment
  sampling controls.

**Tool execution boundary (`arabica-runner`)**

- Eight local tools: `read_file`, `write_file`, `edit_files`, `delete_file`,
  `list_dir`, `grep`, `find_files`, `shell`, with workspace-root confinement,
  policy presets (`read_only`, `coding`, `legacy`), shell interaction
  classification, and output truncation.

**Runtime (`arabica-runtime`)**

- Event-sourced `CoreRuntime` engine; canonical event log is the only
  continuation source.
- Short-memory policy, compaction (`disabled`, `pointer_gc`,
  `file_backed_gc` with async GC and effort knobs), archived evidence with
  content-hashed `memory_read` pointers.
- Long-memory manager with `FileArchiveStore` and `SqliteArchiveStore`.
- Permission gate (`ToolPermissionGate`, rules and decisions) and run
  cancellation control.

**Session & persistence**

- `arabica-session`: session lifecycle, history, sequencing.
- `arabica-adapters`: JSONL observer/store adapters implementing the session
  ports.

**Experiments**

- `benchmarks/` with the short-memory benchmark, CLI comparison protocol, and
  protocol fixtures; `paper/` and `docs/research-radar/` track the research
  program.

---

## P1 — Correctness and hardening of existing surfaces

Short horizon. These items remove known gaps in shipped behavior before new
capability is added.

- [ ] **Per-session concurrency in the server.** The Axum host serializes all
      sessions through one `Arc<Mutex<SessionManager>>`. Move to per-session
      isolation so one slow run cannot block unrelated sessions, while
      preserving per-session command ordering.
- [ ] **SSE replay and resume.** `/v1/events` is a live broadcast only: a
      subscriber that connects after events are emitted misses them. Add a
      `Last-Event-ID`-style replay window backed by the session event log, and
      document reconnect semantics.
- [ ] **Provider cancellation semantics.** `ModelProvider::cancel` currently
      only clears bookkeeping because the trait awaits the run while borrowing
      the adapter. Land the planned background-dispatch/event-sink revision so
      cancellation actually aborts in-flight HTTP work.
- [ ] **Content-block coverage.** Non-text content blocks are rejected at
      encode time ("cannot yet losslessly encode non-text content blocks").
      Define the lossless image/tool-artifact story or keep the typed refusal
      and document it as a protocol invariant.
- [ ] **Security review of the shell boundary.** Re-audit command
      classification, scrubbing, and workspace escape resistance in
      `arabica-runner`; add conformance tests for hostile inputs
      (path traversal, shell metacharacters, symlinked roots).

## P2 — Provider layer completion

- [ ] **Gemini adapters.** Implement `GeminiGenerateContent` and
      `GeminiInteractions` codecs behind the existing `ApiCodec` trait so
      runtime/session code never branches on providers.
- [ ] **Streaming for OpenAI Responses.** Bring the Responses dialect to
      parity with Chat Completions streaming (deltas for message and
      reasoning via `ModelProgressSink`).
- [ ] **Structured output / strict schemas.** Exercise `strict` tool schemas
      and add a documented story for provider-side structured output modes.
- [ ] **Provider conformance suite.** A shared mock-endpoint test harness
      (request encoding, response decoding, stream assembly, error mapping,
      usage extraction) so every dialect proves the same contract; promote the
      ad-hoc mock Axum servers into reusable fixtures.

## P3 — Memory and context program

The research core. Items here should land together with benchmark evidence.

- [ ] **Long-memory retrieval policy.** `LongMemoryManager` currently exposes
      context selection to the runtime; evaluate and document retrieval
      strategies (recency, relevance, disclosure level) with measurable
      baselines in the benchmark package.
- [ ] **Compaction strategy evaluation.** Compare `disabled`, `pointer_gc`,
      and `file_backed_gc` (including effort and continuation-probability
      knobs) on token cost, cache churn, and task success; publish the
      protocol and results under `docs/` and `benchmarks/`.
- [ ] **Pointer GC admission learning.** `PointerGcAdmissionPolicy` and its
      observation sinks are in place; use collected observations to tune
      admission thresholds instead of fixed heuristics.
- [ ] **Cross-session memory boundaries.** Define and test what survives
      session end versus run end, and how archived evidence is garbage
      collected without breaking old pointer hashes.

## P4 — Protocol and transport maturity

- [ ] **Session query endpoints.** Read-only HTTP surface for session
      history, event ranges, and archive lookup (projection only; session
      sequencing stays in `arabica-session`).
- [ ] **Transport-level auth.** The server currently assumes a trusted local
      network. Add an optional token/mTLS story without ever logging
      credentials.
- [ ] **Backpressure and event buffering policy.** The broadcast channel is
      bounded at 512; define and test behavior under slow consumers.
- [ ] **Schema versioning policy.** `/v1/schema` exports the source of truth;
      formalize additive-change rules and a compatibility checklist for
      protocol freezes.

## P5 — Runner and tool ecosystem

- [ ] **External tool registration.** A typed path for hosts to register
      additional tools (schemas from `schemars`, results mapped to
      `ToolResultItem`) without weakening the runner trait boundary.
- [ ] **MCP parity in the runtime.** The CLI has an `mcp` module; evaluate
      promoting MCP-backed tools into the runtime tool-selection path so all
      hosts benefit.
- [ ] **Permission UX for headless hosts.** `ToolPermissionGate` exists;
      expose permission decisions as canonical events so remote clients can
      approve/deny interactively.

## P6 — Ops, packaging, and release engineering

- [ ] **Deployment story.** Grow `deploy/` into a documented reference
      deployment (container image, health checks, env-var contract, archive
      volume layout).
- [ ] **Observability.** Structured, secret-free logging of run lifecycle and
      usage; optional metrics endpoint. Provider raw-exchange capture already
      exists for deep debugging — keep it opt-in and document retention.
- [ ] **CI gates.** Ensure CI enforces the full gate set:
      `cargo fmt --all --check`, `cargo clippy --workspace --all-targets --
      -D warnings`, `cargo test --workspace`, and the `no unsafe` guarantee.
- [ ] **Documentation pass.** Link `docs/runtime_core_architecture.md`
      decisions from this roadmap. (`AGENTS.md` crate ownership was already
      reconciled with the workspace: `arabica-adapters` and `arabica-cli`
      are listed, and the non-member `benchmarks/` subdirectories are
      annotated.)

---

## Non-goals

- No frontend, TUI productization, or UI dependencies (the CLI TUI is a
  transport for the protocol, not a product surface).
- No second implementation of the runtime, protocol, or experiment framework.
- No provider-specific branching outside `arabica-provider`.
- No silent protocol compatibility breaks; additive changes with schema
  updates only.

## Maintenance

- Order inside a priority level is flexible; do not start a later level by
  leaving earlier correctness gaps open.
- Every roadmap item that changes behavior ships with focused unit or
  conformance tests and passes the full gate set.
- When an item lands, check it off here and record the decision in the
  relevant `docs/` file.
