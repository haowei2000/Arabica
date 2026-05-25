# Local Agent Event Loop

Structure local surfaces use an embedded Rust runtime instead of a local web
service. The CLI, TUI, and Tauri desktop app call the same runtime crate:

```text
crates/structure-local-runtime
  -> SQLite event store
  -> workspace metadata
  -> knowledge source registry
  -> artifact writer
  -> deterministic local run loop
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

The first local loop is intentionally deterministic. It does not pretend to be
a full LLM executor yet; instead it establishes the same event-sourced lifecycle
that future local model/tool execution will extend.

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
  explicit instead of relying only on implicit `--workspace` creation.
- CLI parity checks: `uv run structure parity --json` reads the shared
  capability matrix, while `uv run structure parity --verify --json` verifies
  surface coverage, primitive references, entrypoints, and evidence fields.
- CLI benchmarks: `uv run structure bench local` runs the embedded benchmark
  loop and writes `summary.json` / `summary.md` reports. `uv run structure
  bench evidence <summary.json> --json` relinks a report to its run evidence,
  event counts, tool calls, and artifacts.
- CLI evidence: `uv run structure evidence bundle --json` exports one
  reproducible local bundle containing the manifest snapshot, parity report,
  workspace replay, run evidence, knowledge source metadata, artifacts, and
  recent benchmark reports.
- TUI: operator view plus active workspace switching bound to `w`, workspace
  create/open input bound to `o`, custom prompt input bound to `c`, knowledge
  path registration bound to `s`, knowledge registration removal bound to `x`,
  a local workspace check bound to `n`, and a local benchmark run bound to `b`;
  it also surfaces knowledge and artifact previews through the same runtime.
  Press `e` to inspect the same local evidence bundle.
- Desktop app: Tauri commands such as `local_agent_run`, `local_runs`, and
  `local_run_events`; `create_local_workspace` and `local_workspaces` back the
  native workspace selector. `local_benchmark_run` starts the same embedded
  benchmark loop from the desktop shell, `local_benchmark_evidence` relinks
  selected reports to their run evidence, and `local_evidence_bundle` exposes
  the same reproducibility artifact as the CLI/TUI.

This keeps the conceptual model aligned with the distributed web backend:
event-sourced run lifecycle, workspace-scoped context, knowledge retrieval, and
artifact provenance. The local implementation is embedded; the web
implementation remains service-based.
