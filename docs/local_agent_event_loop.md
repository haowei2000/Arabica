# Local Agent Event Loop

Structure local surfaces use an embedded Rust runtime instead of a local web
service. The CLI, TUI, and Tauri desktop app call the same runtime crate:

```text
crates/structure-local-runtime
  -> SQLite event store
  -> workspace metadata
  -> knowledge source registry
  -> artifact writer
  -> OpenAI-compatible or deterministic local run loop
```

## Storage Decision

The local runtime uses a hybrid storage model:

- SQLite is canonical for events, runs, workspace metadata, and knowledge
  source indexes.
- The filesystem is canonical for original files and generated artifacts.
- Indexes are derived and can be rebuilt from files plus SQLite metadata.

By default the runtime writes to:

```text
.structure/local/
  structure.db
  artifacts/<run_id>/response.md
```

`STRUCTURE_LOCAL_RUNTIME_DIR` can override that directory for tests or custom
installations.

## Event Loop

The local loop is event-sourced regardless of model provider. When
`OPENAI__API_KEY`, `OPENAI__BASE_URL`, and `OPENAI__MODEL` are configured, the
OpenAI-compatible provider plans local tool calls through native `tool_calls`
and can iterate over prior tool results before the final synthesis. Without
those variables, the runtime falls back to a deterministic planner/synthesizer
for offline development and tests.

Local tool execution includes safe repository listing/search/reads, knowledge
source reads, and allowlisted local verification commands through
`run_local_command`. Command execution never uses a shell, resolves cwd under
the repository root, clamps runtime/output limits, and records stdout, stderr,
exit status, timeout state, and truncation metadata as ordinary tool evidence.

Code-agent mode persists a reviewed code-change proposal artifact before any
write. Applying that proposal parses its unified diff, verifies target context
inside the repository root, refuses sensitive configuration targets, and records
the explicit `code_change_applied` event. The deterministic offline proposal
targets `docs/local-code-agent-proposal.md` so smoke tests and demos do not
mutate inspected source files.

The same event log is also exposed as a cursor-based workspace feed through
`workspace_event_feed`. CLI and desktop callers can request events after a known
sequence number and receive `next_after_sequence`, which gives local app/TUI
surfaces an in-process analogue of the service SSE replay cursor without
starting an API server. The desktop shell uses this cursor both for manual Poll
Events and for short-lived live polling while a local agent run is in flight.
The human-readable CLI uses the same cursor to print live event progress during
non-JSON `run` and `chat` execution while preserving machine-readable JSON
output for scripts.

Current event sequence:

```text
WorkspaceOpened
RunCreated
PromptReceived
WorkspaceContextLoaded
KnowledgeRetrieved
ModelRequested          # tool_planning
ModelResponded          # planned LocalToolCall list
ToolCallRequested
ToolCallCompleted
ModelRequested          # optional next planning iteration with tool results
ModelResponded          # optional additional LocalToolCall list or no-op
ModelRequested          # response_synthesis
ModelResponded          # final assistant response
ArtifactWritten
RunFinished
```

Failure handling records `RunFailed` around model/tool execution boundaries.
Individual tool failures are captured as `ToolCallCompleted` events with
`success = false` so the model can still synthesize a grounded response from
partial evidence.

## Surface Responsibilities

The runtime owns state transitions. Surfaces only translate user interaction
into runtime calls:

- CLI: `uv run structure run`, `uv run structure runs`, and
  `uv run structure knowledge` delegate to the Rust `structure-local` binary.
  `uv run structure workspace create/list/show/replay` makes workspace metadata
  explicit instead of relying only on implicit `--workspace` creation. Non-JSON
  `run` and `chat` commands poll the local cursor feed while the run executes.
- CLI parity checks: `uv run structure parity --json` reads the shared
  capability matrix, while `uv run structure parity --verify --json` verifies
  surface coverage, primitive references, entrypoints, and evidence fields.
- CLI evidence: `uv run structure evidence bundle --json` exports one
  reproducible local bundle containing the manifest snapshot, parity report,
  workspace replay, run evidence, knowledge source metadata, and artifacts.
- CLI event feed: `uv run structure workspace events --workspace <id> --after
  <sequence> --json` returns a cursor-based feed over the same SQLite event log
  used by run replay.
- Benchmarks: benchmark execution and report relinking are owned by dedicated
  adapters under `benchmarks/adapters/`, not by the local CLI/TUI or desktop
  app surfaces.
- TUI: operator view plus active workspace switching bound to `w`, workspace
  create/open input bound to `o`, custom prompt input bound to `c`, knowledge
  path registration bound to `s`, knowledge registration removal bound to `x`,
  allowlisted local command execution bound to `!`, and a local workspace check
  bound to `n`; it also surfaces knowledge and artifact previews through the
  same runtime. Press `e` to inspect the same local evidence bundle.
- Desktop app: Tauri commands such as `local_agent_run`, `local_runs`, and
  `local_run_events`; `create_local_workspace` and `local_workspaces` back the
  native workspace selector. `local_workspace_event_feed` powers the Poll Events
  action and live in-flight run polling from a persisted cursor. Repository
  tool controls call safe list/search/read commands plus `run_local_command` for
  allowlisted local checks, and the UI renders an inspectable latest-run event
  trace from `local_run_events`. `local_evidence_bundle` exposes the same
  reproducibility artifact as the CLI/TUI. Benchmark execution stays in the
  dedicated Python adapters under `benchmarks/adapters/`.

This keeps the conceptual model aligned with the distributed web backend:
event-sourced run lifecycle, workspace-scoped context, knowledge retrieval, and
artifact provenance. The local implementation is embedded; the web
implementation remains service-based.
