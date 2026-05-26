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
for offline development and tests. The Rust test suite also runs the env-selected
API path against a local OpenAI-compatible mock server, covering tool planning,
local tool execution, response synthesis, event persistence, and artifact
creation without requiring a paid external account.

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
output for scripts. Manual CLI session tools, TUI commands, and desktop
repository tools also append workspace-scoped `ToolCallRequested` and
`ToolCallCompleted` events with no run id, so operator actions remain visible in
the same local event log.

Current event sequence:

```text
WorkspaceOpened
RunCreated
ChatMessageRecorded     # user prompt
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
ChatMessageRecorded     # assistant response
ArtifactWritten         # assistant response artifact
CodeChangeProposed      # code-agent mode only
ArtifactWritten         # proposal artifact in code-agent mode
RunFinished
```

Failure handling records `RunFailed` around model/tool execution boundaries.
Individual tool failures are captured as `ToolCallCompleted` events with
`success = false` so the model can still synthesize a grounded response from
partial evidence.
For desktop clients, `local_agent_run_attempt` returns the failed `RunSummary`
and its events after a post-creation model/API failure, allowing the UI to show
the same event trace that would be available for a successful run.

## Surface Responsibilities

The runtime owns state transitions. Surfaces only translate user interaction
into runtime calls:

- CLI: `uv run structure run`, `uv run structure runs`, and
  `uv run structure knowledge` delegate to the Rust `structure-local` binary.
  `uv run structure workspace create/list/show/replay` makes workspace metadata
  explicit instead of relying only on implicit `--workspace` creation. Non-JSON
  `run` and `chat` commands poll the local cursor feed while the run executes,
  and interactive `/ls`, `/search`, `/read`, and `/cmd` calls are recorded as
  workspace tool events. CLI `run` and one-shot `chat` return `RunAttempt` in
  JSON mode, so successful and failed runs both keep the same inspectable event
  trace contract as the desktop app.
- CLI parity checks: `uv run structure parity --json` reads the shared
  capability matrix, while `uv run structure parity --verify --json` verifies
  surface coverage, primitive references, entrypoints, and evidence fields.
- CLI evidence: `uv run structure evidence bundle --json` exports one
  reproducible local bundle containing the manifest snapshot, parity report,
  workspace replay, run evidence, knowledge source metadata, and artifacts.
- CLI event feed: `uv run structure workspace events --workspace <id> --after
  <sequence> --json` returns a cursor-based feed over the same SQLite event log
  used by run replay. Each local event is enriched with `canonical_flow_id` and
  `primitive_id`, mapping the local run trajectory back to
  `core/structure_core.json` (`goal`, `address`, `disclose`, `event`,
  `evidence`, and `feedback`).
- CLI transcript: `uv run structure runs transcript <run-id> --json` returns the
  shared run inspection object used by both local surfaces. It combines the run
  summary, chat turn, ordered events, evidence summary, final response, and
  core flow/primitive taxonomy without re-stitching those concepts in each UI.
- CLI session inspection: inside `uv run structure chat`, `/select <run-id>`,
  `/last`, `/transcript [run-id]`, `/inspect [run-id]`, `/proposal`, and
  `/apply --dry-run` make run inspection and proposal review part of the live
  agent terminal instead of a separate dashboard workflow. Interactive
  `/apply` defaults to dry-run; writing requires an explicit `--yes`.
- Benchmarks: benchmark execution and report relinking are owned by dedicated
  adapters under `benchmarks/adapters/`, not by the local CLI/TUI or desktop
  app surfaces.
- TUI: operator view plus active workspace switching bound to `w`, workspace
  create/open input bound to `o`, custom prompt input bound to `c`, knowledge
  path registration bound to `s`, knowledge registration removal bound to `x`,
  allowlisted local command execution bound to `!`, and a local workspace check
  bound to `n`; `!` commands are persisted as workspace tool events. It also
  surfaces knowledge and artifact previews through the same runtime. Press `t`
  to inspect the selected run transcript and `e` to inspect the same local
  evidence bundle. Proposal controls `g`, `u`, and `y` prefer the selected
  run's code-change proposal before falling back to the latest workspace
  proposal, preserving the inspect-review-apply loop around a chosen run.
- Desktop app: Tauri commands such as `local_agent_run_attempt`, `local_runs`,
  `local_run_transcript`, and `local_run_events`; `create_local_workspace` and
  `local_workspaces` back the native workspace selector.
  `local_workspace_event_feed` powers the Poll Events action and live in-flight
  run polling from a persisted cursor.
  The composer accepts slash commands such as `/mode`, `/workspace`, `/runs`,
  `/transcript`, `/search`, `/read`, `/source`, `/artifacts`, `/proposal`, and
  `/apply --dry-run`, so desktop interaction can stay in the chat/code-agent
  loop instead of becoming a separate operator dashboard. Command results are
  appended to the desktop chat thread as local interaction turns while
  persisted agent runs still come from the Rust runtime event store.
  Proposal preview/apply controls prefer the selected run, matching the
  transcript and event-trace selection model; dry-run commands display the
  reviewed preview before writing.
  Repository tool controls call safe list/search/read commands plus
  `run_local_command` for allowlisted local checks; those manual tool calls are
  persisted as workspace events, and the UI renders both workspace feed events
  and an inspectable latest-run transcript from successful or failed attempts.
  `local_evidence_bundle` exposes the same reproducibility artifact as the
  CLI/TUI. Benchmark execution stays in the dedicated Python adapters under
  `benchmarks/adapters/`.

This keeps the conceptual model aligned with the distributed web backend:
event-sourced run lifecycle, workspace-scoped context, knowledge retrieval, and
artifact provenance. The local implementation is embedded; the web
implementation remains service-based.
