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

- SQLite is canonical for events, runs, workspace metadata, local tasks, and
  knowledge source indexes.
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
`uv run structure llm check`, the interactive CLI `/llm` command, and the
desktop `/llm` command issue a small chat-completions request through the same
Rust runtime, so environment-backed API connectivity is tested without exposing
the API key.

Local tool execution includes safe repository listing/search/reads, knowledge
source reads, and allowlisted local verification commands through
`run_local_command`. Command execution never uses a shell, resolves cwd under
the repository root, clamps runtime/output limits, and records stdout, stderr,
exit status, timeout state, and truncation metadata as ordinary tool evidence.
The command allowlist is intentionally non-mutating for formatter/linter paths:
`cargo fmt` is accepted only with `--check`, `uv run ruff format` is accepted
only with `--check`, and fix/write/force-style arguments are refused. This keeps
CLI/TUI/desktop code-agent runs in the Structure proposal-and-evidence loop
instead of letting a model silently rewrite the working tree through a
verification tool.
At run start, the shared runtime also loads root-level `AGENTS.md` as
workspace instructions when present. Those instructions are written into
`WorkspaceContextLoaded`, passed into OpenAI-compatible planning and synthesis,
and promoted into run evidence as instruction paths. That gives the CLI, TUI,
and desktop app the same repository instruction semantics without each surface
implementing its own prompt prelude.
The same `WorkspaceContextLoaded` event also records a git worktree snapshot
when available: branch, clean/dirty state, and bounded changed-file entries.
That snapshot is now a first-class local surface as well: `structure worktree`
prints the current branch and changed files, the TUI `d` key opens the same
snapshot in the preview pane, interactive CLI sessions expose `/worktree` and
`/dirty`, and the desktop app exposes a Worktree panel whose changed-file rows
can be opened and attached to the next agent prompt.
That snapshot is passed to planning and synthesis, then promoted into run
evidence, so local code-agent proposals can account for existing user changes
before suggesting a patch.
Prompts can also include Codex-style `@repo/relative/path`,
`@repo/relative/path:line`, and `@repo/relative/path#Lline` references. The
runtime resolves those references as `read_repo_file` tool calls before model
planning, so CLI, TUI, and desktop app all get the same addressed file context
through Structure events instead of surface-specific prompt rewriting.
Resolved prompt references are promoted into `RunEvidenceSummary`, so terminal
evidence output and the desktop selected-run transcript can show the file
context used by a run without requiring raw event inspection.
`ModelRequested` and `ModelResponded` events are also promoted into a
`model_usage` summary that counts model requests, responses, network-backed
requests, standard OpenAI-compatible token usage when available, and final
model response characters. This keeps CLI/TUI/desktop inspection aligned with
cost-ledger inputs without making any surface parse provider-specific response
JSON.

Code-agent mode persists a reviewed code-change proposal artifact before any
write. If the model response includes a safe fenced unified diff, that model
diff becomes the proposal artifact with `proposal_source=model_diff`; otherwise
the runtime writes a deterministic fallback proposal with
`proposal_source=runtime_fallback`. Applying a proposal parses its unified diff,
verifies target context inside the repository root, refuses sensitive
configuration targets, writes a rollback backup artifact before changing the
target, and records the explicit `code_change_applied` event. Rolling back an
applied proposal restores that backup or removes a newly-created target, then
records `code_change_reverted`.
The deterministic offline fallback targets `docs/local-code-agent-proposal.md`
so smoke tests and demos do not mutate inspected source files.

The same event log is also exposed as a cursor-based workspace feed through
`workspace_event_feed`. CLI and desktop callers can request events after a known
sequence number and receive `next_after_sequence`, which gives local app/TUI
surfaces an in-process analogue of the service SSE replay cursor without
starting an API server. The desktop shell uses this cursor both for the manual
Workspace Events action and for short-lived live polling while a local agent run
is in flight.
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
WorkspaceContextLoaded  # AGENTS.md instructions, git worktree, recent turns, workspace meta
KnowledgeRetrieved
ModelRequested          # tool_planning, network marker
ModelResponded          # planned LocalToolCall list, optional token usage
ToolCallRequested
ToolCallCompleted
ModelRequested          # optional next planning iteration with tool results
ModelResponded          # optional additional LocalToolCall list or no-op, optional token usage
ModelRequested          # response_synthesis
ModelResponded          # final assistant response, optional token usage
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
- Noninteractive CLI continuation: `uv run structure continue <run-id>
  [instruction]` is the scriptable counterpart to interactive `/continue`,
  TUI `f`, and desktop Continue. It loads the source run transcript/evidence,
  derives the same compact continuation context exposed by `runs compact`,
  creates a fresh run through the shared `ContinuationRequest`, and can emit the
  same `RunAttempt` JSON envelope for automation.
- Noninteractive workspace/session continuation:
  `uv run structure workspace continue --workspace <id> [instruction]` is the
  scriptable counterpart to interactive `/session-continue`, TUI `F`, and
  desktop Session Continue. It derives compact handoff context from workspace
  replay, recent run compacts, knowledge sources, artifacts, and Core trace
  metadata, then creates a fresh run through the shared
  `WorkspaceContinuationRequest`.
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
  `evidence`, and `feedback`). `RunEvidenceSummary` also includes a
  `CoreExecutionTrace` that validates those flow and primitive references
  against the current Structure core manifest and marks the run `core_aligned`
  only when no drift is detected.
- Core write guard: `SqliteLocalStore::append_event` validates each event
  kind's canonical flow and primitive mapping against the current Structure core
  manifest before persisting it, so CLI/TUI and desktop cannot silently write a
  local event stream that has drifted away from the paper-facing core contract.
- CLI transcript: `uv run structure runs transcript <run-id> --json` returns the
  shared run inspection object used by both local surfaces. It combines the run
  summary, chat turn, ordered events, evidence summary, final response, and
  core flow/primitive taxonomy without re-stitching those concepts in each UI.
- CLI run plan: `uv run structure runs plan <run-id> --json` returns a compact
  Codex/OpenCode-style progress view derived from `agent_step_planned`,
  `model_requested`, and `tool_call_requested` events. The source of truth
  remains the immutable Structure event stream.
- CLI run status: `uv run structure runs status <run-id> --json` returns a
  single-run status snapshot with the latest event, terminal state, tool
  failures/pending calls, model usage, artifact pointers, Core alignment, and
  next actions. Interactive `/run-status` and TUI `i` render the same runtime
  object.
- CLI compact context: `uv run structure runs compact <run-id> --json` returns a
  continuation-ready summary derived from the same run transcript, evidence,
  artifacts, model usage, and Core trace. It is the local equivalent of a
  Codex-style context compaction boundary, but the boundary is computed from
  Structure events rather than a detached chat buffer.
- CLI workspace compact: `uv run structure workspace compact --workspace <id>
  --json` returns a session handoff object derived from workspace replay,
  recent run compacts, knowledge sources, artifacts, and Core flow/primitive
  paths. Interactive `/session` renders the same object inside the local agent
  terminal.
- CLI workspace/session continue: `uv run structure workspace continue
  --workspace <id> --json [instruction]` turns that handoff object into a new
  event-sourced run. This keeps long local sessions aligned with Structure Core
  instead of passing around a desktop- or terminal-specific chat buffer.
- CLI workspace/session usage: `uv run structure workspace usage --workspace
  <id> --json` aggregates recent run evidence into session-level totals for
  immutable events, local tools, artifacts, knowledge, Core alignment, model
  network requests, and tokens. Interactive `/session-usage` renders the same
  object in the local agent terminal.
- CLI core trace: `uv run structure runs trace <run-id> --json` returns the
  paper-facing execution path derived from the same immutable events. It keeps
  the collapsed Structure flow path (`goal -> address -> ...`) and primitive
  path beside per-event payload summaries, so automation can verify the local
  loop is still using Structure Core rather than a surface-specific scheduler.
- CLI run review: `uv run structure runs review <run-id> --json` returns the
  shared post-run review object used by local surfaces. It combines Core
  alignment, flow/primitive paths, model/tool counts, failed tool count,
  response/proposal artifacts, and next actions such as dry-run/apply or
  continue.
- CLI human checkpoint: `uv run structure runs checkpoint <run-id> <note...>`
  and interactive `/checkpoint [run-id] <note>` append a
  `run_checkpoint_recorded` feedback event to the selected run. Run compacts and
  continuations carry those checkpoint notes forward, so operator decisions are
  part of the Structure event boundary instead of a detached chat aside.
- CLI tasks: `uv run structure tasks list/add/update/done` stores local
  workspace tasks in the Rust runtime and records `task_created` /
  `task_updated` events. Tasks can be linked to the selected run, so planning
  and follow-up work stay in the same Structure event ledger as chat, tools,
  evidence, and proposals. The runtime also replays these tasks into
  `local_agent_context`, workspace replay, workspace compact handoff, workspace
  usage, and the OpenAI-compatible model prompt.
- CLI context preview: `uv run structure context --workspace <id> --mode
  code_agent --json` materializes the same assembled Rust context used before
  model planning, without starting a run.
- CLI doctor: `uv run structure doctor --workspace <id> --mode code_agent
  --json` combines the local snapshot, real `OPENAI__` diagnostic, Structure
  Core parity report, and assembled agent context as a pre-run health gate.
- CLI session inspection: inside `uv run structure chat`, `/help` and `/map`
  render a grouped agent command map for run loop, workspace context, run
  evidence, session, and proposal actions while keeping benchmark work outside
  the local agent surface. `/select <run-id>`,
  `/last`, `/history`, `/rerun [n]`, `/continue [run-id] [instruction]`, `/resume`,
  `/status`, `/llm`, `/doctor`, `/context`, `/tasks`, `/task`, `/task-status`, `/task-done`, `/usage [run-id]`,
  `/review [run-id]`, `/plan [run-id]`, `/trace [run-id]`, `/transcript [run-id]`, `/inspect [run-id]`, `/proposal`, `/diff`,
  `/risk`, `/dry-run`, and `/apply` make run inspection, continuation, and proposal review part of
  the live agent terminal instead of a separate dashboard workflow. `/continue`
  starts a new Structure local run from the selected run's transcript and
  evidence summary, preserving the event-sourced audit trail instead of editing
  an old run in place. Successful interactive slash commands are also persisted
  through `LocalAgentRuntime::record_command_turn` as `command_turn_recorded`
  workspace events, so the terminal agent loop has the same durable command
  audit trail as the desktop chat.
  `uv run structure retry <run-id>` and interactive `/retry [run-id]` use the
  same continuation request path with an explicit retry instruction, so a failed
  or unsatisfactory run can be tried again as a new audited run while preserving
  the old transcript and events.
  `/context` renders the same Rust-built context snapshot used before model
  planning: workspace id, mode, AGENTS instructions, git worktree, knowledge
  sources, and replayed assistant turns.
  `/history` renders persisted chat/code-agent turns from
  `LocalAgentRuntime::chat_turns`, so session recall is grounded in stored
  Structure runs rather than a separate terminal buffer. `/rerun [n]` selects
  one of those persisted history prompts and starts a fresh Structure local run
  through `run_prompt_attempt`, while the `/rerun` command itself is recorded as
  a `command_turn_recorded` workspace event.
  `/usage` is the lightweight model-call view over the same
  `RunEvidenceSummary`, so terminal sessions can inspect request counts,
  network-backed calls, token usage, and core alignment without opening the
  full transcript.
  `/gc [run-id] [retain-last]` previews the event-level garbage-collection
  visibility filter for a run. It reports retained and filtered event sequence
  ranges without deleting or rewriting the underlying event store.
  `/tools [run-id]` renders the paired local tool trace from
  `tool_call_requested` and `tool_call_completed` events, including tool input,
  result status, compact output, and error text.
  `/plan [run-id]` renders the event-derived agent plan from
  `agent_step_planned` events, so terminal users can inspect progress without
  a separate planner state.
  `/trace [run-id]` renders the same Core flow/primitive execution path as
  `runs trace`, preserving the distinction between raw event replay and
  paper-facing conceptual trace inspection.
  `/review [run-id]` renders the same post-run review as `runs review`, giving
  interactive sessions a compact Codex-style summary before deciding whether
  to inspect tools, continue, dry-run, or apply.
  `/checkpoint [run-id] <note>` records a human decision as a
  `run_checkpoint_recorded` event for the selected run; `/compact` and
  `/continue` replay those notes as part of the durable continuation boundary.
  `/doctor` combines the local session snapshot, the same `OPENAI__`
  diagnostic used by `/llm`, and the Structure Core parity verifier without
  starting an agent run.
  Interactive `/risk [artifact-id|run-id]` and
  `uv run structure proposals review <artifact-id>` parse the same reviewed
  proposal artifact used by apply, verify target safety, new-file conflicts,
  and patch context against the current workspace, persist the review as a
  `code_change_reviewed` Structure event, and report whether apply can proceed
  through the explicit approval gate.
  Interactive `/dry-run` and `/apply` without `--yes` preview proposal
  application; writing requires an explicit `/apply --yes`. Interactive
  `/rollback` and `uv run structure proposals rollback <artifact-id>` restore
  the backup captured before apply.
  `/remember <text>` persists a short text note into the local workspace
  knowledge directory and registers it as a normal `KnowledgeSource`, so later
  runs retrieve it through the same Structure context path as file-backed
  knowledge. `/recall <source-id>` previews the stored source, and `/forget
  <source-id>` removes a registered source and records the same workspace
  context-change event path.
  `/rate <source-id> <1-5> [note]` and `uv run structure knowledge rate
  <source-id> <1-5>` record a `source_rated` event for the selected run when
  available, mapping the local feedback loop back to the `source_evaluation`
  primitive in the Structure Core manifest. Later local agent runs replay the
  latest source ratings while assembling `LocalAgentContext`, prioritizing
  highly rated knowledge before model planning and demoting low-rated sources.
- CLI mode selection: noninteractive `uv run structure chat` and
  `uv run structure run` accept `--mode chat|code_agent` so scripts can choose
  conversational or code-agent behavior without bypassing the same
  event-sourced runtime. The older `chat --chat-only` flag remains as a
  compatibility alias for `--mode chat`.
- API model synthesis: when an `OPENAI__` model echoes the Structure response
  envelope, the Rust runtime strips that nested envelope before persisting the
  assistant response. CLI, TUI, and desktop therefore share one clean
  event-loop response format instead of patching duplicated headers per
  surface.
- Benchmarks: benchmark execution and report relinking are owned by dedicated
  adapters under `benchmarks/adapters/`, not by the local CLI/TUI or desktop
  app surfaces.
- TUI: operator view plus active workspace switching bound to `w`, workspace
  create/open input bound to `o`, custom prompt input bound to `c`, knowledge
  path registration bound to `s`, knowledge registration removal bound to `x`,
  allowlisted local command execution bound to `!`, and a local workspace check
  bound to `n`; `!` commands are persisted as workspace tool events. `R` records
  a source rating for the selected run or workspace, and `D` records a selected
  run human decision as the same `run_checkpoint_recorded` event used by CLI
  `/checkpoint` and desktop `/checkpoint`. `?` opens a command map grouped by
  run loop, workspace context, run evidence, session, and proposal actions, so
  the TUI presents a Codex/OpenCode-style agent console rather than a flat
  dashboard. The inspect actions `?`, `A`, `H`, `E`, and `W` are also persisted through
  `LocalAgentRuntime::record_command_turn` as `command_turn_recorded` workspace
  events, so TUI navigation has the same durable command audit trail as CLI and
  desktop chat interactions. `A` previews the assembled
  `LocalAgentContext` without starting a run, showing the same AGENTS.md,
  worktree, knowledge, source-rating, task, and recent-turn context that CLI
  `/context` and desktop Agent Context materialize before planning. `H` opens a
  local Doctor report that combines the `OPENAI__` diagnostic, Structure Core
  parity checks, and the same assembled context, matching CLI `/doctor` and
  desktop Doctor without starting a run. It also surfaces knowledge and artifact
  previews through the same runtime. Custom
  prompt and workspace-check runs open a `RunAttempt` preview immediately, so
  successful and failed TUI runs both expose their event trace without leaving
  the local terminal UI. Press `N` to rerun the selected persisted prompt as a
  fresh Structure local run, matching CLI and desktop `/rerun` while using the
  selected TUI row as the history source. Press `f` to continue the selected run through the
  shared continuation request path, preserving the same transcript/evidence
  grounding used by CLI `/continue` and desktop Continue. Press `t` to inspect
  the selected run transcript, `E` to inspect the complete immutable run event
  stream, `l` to inspect the event-derived agent plan,
  `z` to inspect the same Core flow/primitive trace
  as CLI `runs trace`, `b` to inspect the same post-run review as CLI
  `runs review`, `O` to inspect the selected run's paired
  `tool_call_requested` / `tool_call_completed` trace, `W` to inspect the
  active workspace cursor event feed, and `e` to inspect the same local
  evidence bundle.
  Proposal controls `g`, `h`, `u`, and `y` prefer the selected run's code-change
  proposal before falling back to the latest workspace proposal, preserving the
  inspect-risk-review-apply loop around a chosen run.
- Desktop app: a visible Command Map action plus `/help`, `/map`, and
  `/commands` render the same grouped agent-loop mental model as CLI/TUI. The
  visible action is recorded as a `/map` command turn in the chat thread, while
  keeping benchmark execution outside the local app surface. Tauri commands such as `local_session_status`,
  `local_agent_run_attempt`, `local_runs`,
  `local_run_transcript`, `local_run_plan`, `local_run_core_trace`, and `local_run_events`;
  `create_local_workspace` and `local_workspaces` back the native workspace selector.
  A segmented `Chat` / `Code Agent` mode control drives the same `mode` field
  passed to the Rust runtime as `/mode`, keeping visible desktop interaction and
  command-style interaction on one event loop.
  `local_runs` powers both the Recent Runs sidebar and a visible Runs action
  that records `/runs` as command-turn history, so desktop run navigation stays
  inside the same chat/code-agent loop as slash commands.
  `local_run_transcript` powers `/transcript`, `/inspect`, per-turn Inspect,
  and the visible Transcript action; the visible action records
  `/transcript <run-id>` as command-turn history while rendering the selected
  run's chat turn, final response, evidence summary, Core trace metadata, and
  immutable events through the same Rust runtime.
  `local_workspace_event_feed` powers the Workspace Events action,
  `/workspace-events` slash command, and live in-flight run polling from a
  persisted cursor.
  In-flight chat bubbles consume that same feed to show the current live event
  count and latest Structure event while a run or continuation is executing.
  The composer accepts slash commands such as `/status`, `/llm`, `/doctor`, `/mode`, `/workspace`,
  `/runs`, `/history`, `/rerun [n]`, `/continue [run-id] [instruction]`, `/retry [run-id]`, `/transcript`, `/search`,
  `/read`, `/source`, `/artifacts`, `/proposal`, `/diff`, `/gc`, `/tools`, `/plan`, `/compact`, `/session`, `/trace`, `/review`, `/risk`, `/dry-run`, `/apply --dry-run`, and `/rollback`, so
  desktop interaction can stay in the chat/code-agent loop instead of becoming
  a separate operator dashboard. Desktop `/continue` calls the same Rust
  runtime continuation path as the CLI, creating a fresh run from the selected
  run's transcript and evidence. The per-turn Continue action records the same
  `/continue <run-id> ...` command turn after the fresh continuation attempt,
  so visible desktop continuation remains replayable as Structure workspace
  history.
  Desktop `/retry` and the per-turn Retry action use that same continuation
  path with a retry instruction, keeping retry semantics explicit without
  mutating the previous run; the visible Retry action records `/retry <run-id>`
  as a command turn after the fresh retry attempt.
  Desktop `/context` and the visible Agent Context action call the shared
  `local_agent_context` Tauri command so the app shows the same assembled
  context the CLI sees before a run starts; the visible action is also appended
  to the chat thread as a `/context` command turn. Desktop command turns are
  written through `record_local_command_turn` as `command_turn_recorded`
  workspace events and restored through `local_command_turns`, so command
  output survives refresh/reopen as Structure event history rather than a
  browser-only buffer. The desktop composer also rebuilds its prompt/command
  recall history from persisted `local_chat_turns` and `local_command_turns`,
  so app navigation remains grounded in Structure Core run and workspace
  events rather than a frontend-only draft list. Desktop `/rerun [n]` uses
  those persisted `local_chat_turns` as fresh `local_agent_run_attempt` input,
  and each persisted chat/code-agent turn exposes a visible Rerun action that
  feeds the same turn prompt back into `local_agent_run_attempt` and records
  the visible action as a `command_turn_recorded` workspace event. That matches
  CLI `/rerun` and TUI `N` without introducing a desktop-only history model.
  Desktop `/doctor` and the visible Doctor action append the same `/doctor`
  command turn after combining the Rust session status, real `OPENAI__`
  diagnostic, and Core parity report.
  Desktop `/transcript [run-id]`, `/inspect [run-id]`, the visible Transcript
  action, and per-turn Inspect actions mirror the CLI transcript view by
  calling `local_run_transcript` before updating the selected run and event
  timeline; visible Transcript actions are recorded as
  `/transcript <run-id>` command turns.
  Desktop `/usage [run-id]` and per-turn Usage actions mirror the CLI
  model-call shortcut and render the same `model_usage` evidence summary from
  Rust. Per-turn Usage actions are recorded as `/usage <run-id>` command turns.
  Desktop `/gc [run-id] [retain-last]` calls the same non-destructive
  `local_run_event_gc` preview as the CLI, keeping event visibility policy
  inspectable inside the app chat loop.
  Desktop `/events [run-id]` and per-turn Events actions call
  `local_run_events`, making the raw immutable run stream a first-class
  desktop chat action instead of hiding it inside transcript inspection.
  Per-turn Events actions are recorded as `/events <run-id>` command turns.
  Desktop `/tools [run-id]`, the Tool Trace action, and per-turn Tools actions
  call `local_run_tool_trace`, giving the app the same ordered tool-call
  explanation as the CLI/TUI without re-parsing raw events in JavaScript. The
  visible Tool Trace action is also recorded as a `/tools <run-id>` chat command
  turn.
  Desktop `/run-status [run-id]`, the Run Status action, and per-turn Status
  actions call `local_run_status`, giving the app the same latest-event,
  usage, tool, artifact, Core alignment, and next-action snapshot as CLI/TUI;
  the visible Run Status action is recorded as a `/run-status <run-id>` chat
  command turn.
  Desktop `/tasks`, `/task`, `/task-status`, `/task-done`, and the Tasks action
  call `local_tasks`, `create_local_task`, and `update_local_task_status`,
  keeping agent work items as workspace/run-linked Structure events rather than
  a desktop-only checklist. Those same task records are rendered in Agent
  Context, Session Compact, Session Usage, replay, and evidence-bundle views;
  the visible Tasks action is recorded as a `/tasks` command turn.
  Desktop `/plan [run-id]`, the Plan action, and per-turn Plan actions call
  `local_run_plan`, showing progress as a derived view over immutable Structure
  planning/model/tool events rather than a desktop-only state machine. The
  visible Plan action is recorded as a `/plan <run-id>` chat command turn.
  Desktop `/compact [run-id]` and per-turn Compact actions call
  `local_run_compact`, showing the same continuation-ready context boundary as
  CLI/TUI. The app can therefore support long local chat/code-agent sessions
  without inventing a desktop-only memory model.
  Desktop `/session`, `/workspace-compact`, and the Session Compact action call
  `local_workspace_compact`, giving the desktop app the same workspace/session
  handoff object as CLI/TUI while staying inside the local Rust runtime. The
  visible action is recorded as a `/session` command turn.
  Desktop `/session-continue`, `/workspace-continue`, and the Session Continue
  action call `local_workspace_continue_attempt`, turning that compact handoff
  into a fresh run with the same local event loop and Core trace semantics.
  Desktop `/session-usage`, `/workspace-usage`, and the Session Usage action
  call `local_workspace_usage`, showing the same session-level model, token,
  tool, artifact, and Core totals as CLI/TUI; the visible action is recorded as
  a `/session-usage` command turn.
  Desktop `/trace [run-id]`, the Core Trace action, and per-turn Trace actions
  call `local_run_core_trace`, giving the app the same Structure Core
  flow/primitive path as CLI/TUI without introducing a desktop scheduler
  concept. Visible and per-turn Trace actions are recorded as
  `/trace <run-id>` command turns.
  Desktop `/review [run-id]`, the Review action, and per-turn Review actions
  call `local_run_review`, giving the app the same compact attempt review and
  next-action guidance as CLI/TUI. The visible Review action is recorded as a
  `/review <run-id>` chat command turn.
  Desktop `/checkpoint [run-id] <note>`, the visible Record Decision action,
  and each chat turn's Decision action call `record_local_run_checkpoint`,
  giving the app the same event-sourced human decision path as CLI while
  presenting it as a desktop agent workflow. Those notes are carried into run
  compacts and continuations. Visible and per-turn decision actions append
  `/checkpoint <run-id> <note>` command turns after the note is recorded.
  Desktop `/risk [artifact-id|run-id]` and the preview Risk action call
  `review_local_proposal`, giving the app the same target-safety, patch-context,
  and explicit-approval checks as CLI before any proposal apply while recording
  the same `code_change_reviewed` event in the local Structure stream. Preview
  Risk, Dry Run, Apply, and Rollback actions append `/risk <artifact-id>`,
  `/dry-run <artifact-id>`, `/apply <artifact-id>`, and
  `/rollback <artifact-id>` command turns; cancelled apply confirmations are
  recorded as cancelled command turns.
  Desktop `/rollback [artifact-id]` and the preview Rollback action call
  `rollback_local_proposal`, restoring the backup artifact recorded before
  apply and keeping rollback evidence in the local Structure event stream.
  Desktop `/doctor` and the visible Doctor action mirror CLI `/doctor`: they
  gather `local_session_status`, `local_llm_diagnostic`, and
  `core_parity_report` through Tauri commands and render the local health check
  inside the chat/code-agent loop.
  Normal prompt submissions appear immediately as in-flight local chat turns
  while the Rust runtime is executing, then collapse back into persisted
  workspace chat turns after refresh.
  Persisted chat turns carry their originating run id and mode, and the desktop
  thread exposes per-turn inspect/usage/events/plan/tools/trace/compact/review/continue/retry/proposal
  actions so chat history remains a navigable view over run evidence rather than
  a detached transcript. Per-turn Inspect, Status, Usage, Events, Plan, Tools, Trace, Compact, and
  Review actions append `/inspect <run-id>`, `/run-status <run-id>`,
  `/usage <run-id>`, `/events <run-id>`, `/plan <run-id>`, `/tools <run-id>`,
  `/trace <run-id>`, `/compact <run-id>`, and `/review <run-id>` command turns
  while still reading from the Rust local runtime. Per-turn Proposal actions append
  `/proposal <run-id>` command turns while opening the selected run's code-change
  proposal in Preview.
  The per-turn Continue button uses the current composer text as an optional
  continuation instruction, matching the slash-command path while keeping
  normal chat interaction mouse-accessible. Per-turn Continue and Retry append
  `/continue <run-id> ...` and `/retry <run-id>` command turns after their fresh
  Structure continuation attempts.
  Command results are appended to the desktop chat thread as local interaction
  turns while
  persisted agent runs still come from the Rust runtime event store.
  Proposal risk/preview/apply controls prefer the selected run, matching the
  transcript and event-trace selection model. When a code-change proposal is
  open in Preview, the desktop app exposes Risk, Dry Run, and Apply actions
  directly on that review surface, so the user-facing app completes the inspect
  -> risk -> review -> apply loop without falling back to terminal-style
  service controls.
  Desktop `/remember <text>` mirrors the CLI knowledge-memory path and stores
  text as local workspace knowledge before refresh. Desktop `/forget
  <source-id>` removes the same `KnowledgeSource` from the app chat loop.
  Desktop `/recall <source-id>` previews the source content through the same
  Rust command used by the knowledge panel.
  Desktop `/rate <source-id> <1-5> [note]` mirrors CLI `/rate` through
  `rate_local_knowledge_source`, so source usefulness feedback remains an
  event-sourced part of the selected run evidence rather than a detached UI
  annotation, and the Agent Context view shows the replayed ratings that shape
  subsequent knowledge ordering.
  Repository tool controls call safe list/search/read commands plus
  `run_local_command` for allowlisted local checks; those manual tool calls are
  persisted as workspace events. Visible repository controls append `/ls`,
  `/search <query>`, `/read <path>`, and `/cmd <argv>` command turns to the
  chat thread. A `read_repo_file` preview can be attached to the next composer
  message as an `@path` reference, so desktop users get the same addressed
  file-context mechanism as CLI/TUI prompts without hand-copying paths. The UI
  renders both workspace feed events and an inspectable
  latest-run transcript from successful or failed attempts.
  Runs inspection, worktree refresh, workspace replay, artifact preview, Core parity, and
  evidence-bundle controls now append `/worktree`, `/replay`, `/artifact`,
  `/parity`, `/bundle`, `/runs`, and `/workspace-events` command turns to the same chat thread, keeping
  workspace/context inspection inside the agent conversation rather than as a
  detached dashboard; those command turns use the same persisted event path as
  slash-command output.
  `local_evidence_bundle` exposes the same reproducibility artifact as the
  CLI/TUI. Benchmark execution stays in the dedicated Python adapters under
  `benchmarks/adapters/`.

This keeps the conceptual model aligned with the distributed web backend:
event-sourced run lifecycle, workspace-scoped context, knowledge retrieval, and
artifact provenance. The local implementation is embedded; the web
implementation remains service-based.
