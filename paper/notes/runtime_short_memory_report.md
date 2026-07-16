# Runtime Short-Memory Management in Existing Coding Agents

## Purpose

This report compares runtime short-memory management in current coding
agents and positions Structure's event-visibility design against them.
The focus is not long-term project memory such as `CLAUDE.md`,
`AGENTS.md`, repository instructions, or user preferences. The focus is
the working context used during an active run: conversation history,
tool calls, tool results, subagent outputs, transient reasoning,
runtime state, compaction, and replay.

## Executive Summary

Existing coding agents generally manage runtime short memory through
conversation-level compaction, chat-history truncation, repository maps,
or lifecycle hooks. Claude Code and Codex rely heavily on summarising or
compacting long-running sessions when the context window fills. Aider
compresses codebase context through a repository map but still treats
the active chat as the main runtime memory. Goose exposes hooks over
runtime events, but the events mainly trigger automation rather than
serving as the prompt materialisation unit.

Structure differs by making runtime short memory an explicit
event-visibility problem. Every runtime signal is a typed event; related
events are grouped into stable batches; and the prompt materialiser
chooses whether to load full events, compact batch keys, or nothing.
The audit log remains intact, so compaction changes visibility rather
than deleting or destructively rewriting execution history.

## Structure's Runtime Short-Memory Strategy

Structure separates durable context from runtime short context.
Durable material such as retrieved knowledge, tool schemas, artifacts,
tasks, and remembered facts lives as path-addressable long context.
Runtime short context is represented as typed events:

- `user.message`
- `agent.message`
- `tool.call`
- `tool.result`
- `tool.error`
- `agent.thinking`
- `agent.plan.step`
- `context.using`
- `context.rated`
- `artifact.*`
- `task.*`
- lifecycle events such as `run.created` and `run.completed`

The key idea is that these events are not all automatically visible to
the model. Visibility is controlled before each prompt is assembled.

### Event-Level GC

Event GC is a visibility filter, not deletion. Events remain in the
append-only audit log, but only a subset remains active for prompt
construction.

The default policy:

- Pins durable anchors such as opening user messages, assistant
  decisions, artifact mutations, task completion, and lifecycle
  milestones.
- Keeps tool calls and tool results for a short plan-act window.
- Keeps tool errors longer for recovery.
- Gives transient reasoning short TTLs.
- Drops high-frequency noise such as token deltas and heartbeats from
  prompt visibility.
- Allows expired spans to return as structured `context.summary`
  events.

The implementation also supports event-count TTL and decay rules. This
lets newer events consume the remaining lifetime of older events at
different rates. For example, a `tool.result` can rapidly age out its
matching `tool.call`, and an `agent.message` can rapidly age out
intermediate `agent.thinking`.

### Batched Events

GC alone is too fine-grained, so Structure groups related events into
event batches. A batch is a stable runtime memory unit with a
`context_key`, `context_kind`, sequence span, event counts, token counts,
and compact `key_content`.

Batch kinds include:

- `turn`: user/assistant turn-level context.
- `tool`: tool call plus result or error.
- `context`: operations around a context path.
- `task`: events around a task id.
- `artifact`: events around an artifact id.
- `transient`: noisy or short-lived runtime events.
- `misc`: other low-priority runtime events.

Each batch receives a load state:

- `LOAD_ALL`: load the full events.
- `LOAD_KEY`: load only the batch key, metadata, and compact summary.
- `NO_LOAD`: omit the batch from the prompt.

The policy keeps recent turns and current-run tool batches in
`LOAD_ALL`, downgrades older useful batches to `LOAD_KEY`, and sends
transient or miscellaneous batches to `NO_LOAD`. This makes short
context a structured materialisation problem rather than a single
summarise-or-truncate decision.

## Comparison With Existing Agents

### Claude Code

Claude Code's runtime short-memory strategy is centered on
conversation compaction. When a session approaches the context limit,
Claude Code can summarise the conversation and continue from the
summary. In practice, this treats the previous working context as a
large transcript that must be collapsed into a new summary.

Observed characteristics:

- Runtime memory is primarily the active conversation transcript plus
  tool and subagent outputs.
- `/compact` produces a summary that becomes the new working context.
- Large subagent outputs and heavy tool results can create context
  pressure.
- GitHub issues show user concern about loss of scrollable history,
  compaction failures, and whether important project instructions or
  MCP context survive compaction.

Compared with Structure, Claude Code appears to use a
conversation-level summary strategy. It is effective as a practical
recovery tool, but the runtime units are not exposed as typed events
with explicit per-type visibility policies. The model receives a
summary, but the system does not present a reusable contract like
`LOAD_ALL`, `LOAD_KEY`, or `NO_LOAD` for individual event groups.

### Codex

Codex exposes more inspectable runtime traces through session rollout
JSONL files. Users can inspect session metadata, injected instruction
blocks, and conversation state. Codex also uses compaction or resume
mechanisms in long sessions, and GitHub issues show that users hit
context-window exhaustion or compact-task failures in long-running
sessions.

Observed characteristics:

- Runtime state is recorded in session or rollout JSONL.
- The active context includes base/developer instructions, scoped
  `AGENTS.md` blocks, session metadata, conversation history, and tool
  outputs.
- Long sessions rely on compaction and resume.
- Some user reports indicate that compaction may not always preserve
  project instructions or may fail remotely.

Compared with Structure, Codex is closer to an event-sourced design
because its rollouts make the session auditable. However, the public
interface still centers on session-level context and compaction rather
than a declarative event-visibility policy. The rollout is a record of
what happened; Structure uses the event log as the input to an explicit
prompt materialiser.

### Aider

Aider is strongest in codebase-context compression. Its repository map
summarises important files, classes, functions, types, and call
signatures, giving the model structural awareness of the repository
without loading every file.

Observed characteristics:

- Runtime memory consists of the chat history, selected editable files,
  read-only files, and repository map.
- The repository map is a compact codebase view, not a trajectory
  memory system.
- Users can clear or manage chat context, but runtime tool/event
  history is not represented as typed visibility-controlled events.

Compared with Structure, Aider's compression target is different. Aider
compresses the codebase; Structure compresses runtime event history.
Aider's repo map is analogous to Structure's long-context `glance` or
`overview` layer, but it does not solve the short-memory problem of
which tool results, reasoning steps, and runtime signals should remain
visible.

### Goose

Goose exposes lifecycle hooks for session, prompt, tool, file, and
shell events. This makes Goose closer to an event-driven agent runtime
than purely transcript-based systems.

Observed characteristics:

- Runtime context can be influenced by `.goosehints`, memory extension,
  and lifecycle hooks.
- Hooks can react to runtime events and inject automation.
- Event hooks are mainly extension points, not necessarily the canonical
  representation used to materialise short context.

Compared with Structure, Goose has event awareness but not the same
event-visibility abstraction. Structure treats typed events and event
batches as the direct materialisation substrate for prompt construction.
Goose hooks can observe or modify behavior, but they do not by
themselves define a deterministic `LOAD_ALL` / `LOAD_KEY` / `NO_LOAD`
policy over runtime memory.

## Comparative Table

| System | Runtime short-memory unit | Main compression mechanism | Audit/replay surface | Main limitation relative to Structure |
|---|---|---|---|---|
| Claude Code | Conversation transcript, tool/subagent outputs | Conversation-level compaction summary | Local transcripts and UI history, but compaction may replace visible history | Coarse summary replacement rather than typed event visibility |
| Codex | Session messages, tool outputs, rollout JSONL | Session compaction/resume | Rollout/session JSONL | Auditable trace exists, but public strategy is not a declarative event materialiser |
| Aider | Chat history, selected files, repo map | Repository map and file selection | Chat/history files and git state | Compresses codebase context, not runtime event trajectory |
| Goose | Session context, prompt/tool/file/shell hook events | Hints, memory extension, hooks | Hook and session surfaces | Events are extension triggers, not prompt visibility units |
| Structure | Typed events and event batches | Event GC plus `LOAD_ALL` / `LOAD_KEY` / `NO_LOAD` batch policy | Append-only event log plus archive contexts | Requires careful event taxonomy and batch policy tuning |

## Paper Positioning

The paper should not claim that existing agents lack context
management. They clearly manage context through compaction, scoped
instructions, repo maps, histories, hooks, and memories. The sharper
claim is:

> Existing coding agents commonly manage runtime short memory through
> conversation-level compaction, chat-history truncation, repository
> maps, or lifecycle hooks. Structure instead treats runtime short
> memory as an event-visibility problem: every runtime signal is a typed
> event, related events are grouped into batches, and the prompt
> materialiser decides whether to load full events, compact batch keys,
> or nothing, without deleting the audit log.

This positions Structure as a runtime context materialisation layer
rather than another long-term memory store.

## Cool Papers Similar-Work Check and Timeliness

I checked Cool Papers and adjacent arXiv/OpenReview entries for papers
that are closest to Structure's narrowed claim. The relevant cluster is
active and very recent, which means the topic is timely but the paper
must avoid broad novelty claims about "agent memory" or "context
compression."

### Closest Papers

1. **Context-Folding: Scaling Long-Horizon LLM Agent via
   Context-Folding** (arXiv:2510.11967, published 2025-10-13).
   It gives the agent an explicit folding operation: branch into a
   sub-trajectory, complete the subtask, and collapse the intermediate
   steps into a concise outcome summary. The closest overlap is active
   working-context management for long-horizon tasks. The key
   difference is that Context-Folding learns a procedural folding
   behavior, whereas Structure defines an event-log-backed
   materialisation contract over typed runtime events and batches.

2. **Memory-as-Action / MemAct: Autonomous Context Curation for
   Long-Horizon Agentic Tasks** (arXiv:2510.12635, OpenReview).
   MemAct treats working-memory management as actions inside the
   agent's policy, including operations such as pruning, summarising,
   deleting, and inserting memory paragraphs. This is the strongest
   conceptual competitor because it also targets runtime working
   memory rather than only persistent long-term memory. Structure's
   distinction should be: MemAct edits the active working memory as
   policy actions; Structure keeps the event log immutable and changes
   prompt visibility through deterministic event GC and
   `LOAD_ALL` / `LOAD_KEY` / `NO_LOAD` batch materialisation.

3. **The Context Gathering Decision Process: A POMDP Framework for
   Agentic Search** (arXiv:2605.07042, published 2026-05-07). It
   formalises iterative context gathering as a POMDP and introduces a
   persistent predicate-based belief state plus an exhaustion gate.
   The overlap is explicit infrastructure for bounded working memory
   during search. The difference is that CGDP focuses on search-state
   belief tracking, while Structure focuses on a general runtime event
   substrate for prompts, tools, artifacts, context paths, and replay.

4. **MemAgent: Reshaping Long-Context LLM with Multi-Conv RL-based
   Memory Agent** (ICLR 2026 Oral listing on Cool Papers). MemAgent
   processes long text in segments and overwrites a compact memory
   state, reporting strong extrapolation from 8K context to much
   longer QA tasks. This is important to cite, but it is primarily a
   trained long-context document-processing workflow. It is less
   directly about production agent runtime traces, tool calls, event
   visibility, or audit replay.

5. **Experience Compression Spectrum: Unifying Memory, Skills, and
   Rules in LLM Agents** (arXiv:2604.15877, published 2026-04-17). It
   frames memory, skills, and rules as points on a compression
   spectrum and argues that systems typically operate at fixed
   compression levels. This supports the timeliness of the topic and
   gives Structure a useful contrast: Structure can be presented as
   adaptive compression at the runtime event-batch level, not only as
   episodic memory, skills, or rules.

6. **Memory in the Age of AI Agents** (arXiv:2512.13564, v1
   2025-12-15; v2 2026-01-13). This survey explicitly says that
   traditional long/short-term memory taxonomies are insufficient and
   distinguishes agent memory from RAG and context engineering. It
   confirms that a paper in this area must use precise terminology:
   Structure should say "runtime short-context materialisation" rather
   than generic "agent memory."

### Timeliness Assessment

The paper is still timely, but the safe claim has narrowed.

The area is clearly hot in 2025--2026: Cool Papers already surfaces
Context-Folding, MemAgent, CGDP, and compression-spectrum work, while
survey papers are reorganising the field around memory forms,
functions, dynamics, working memory, and context engineering. This
means reviewers will not accept a claim that "agents need memory" or
"context should be compressed" as novel.

The defensible novelty is more specific:

> Structure treats runtime short context as an event-visibility and
> prompt-materialisation contract. Instead of destructively editing the
> working memory or replacing the transcript with a summary, it stores
> all runtime signals as typed immutable events, groups them into
> event batches, and decides before each model step whether to load full
> events, compact keys, or nothing.

This remains timely because most close papers focus on learned context
folding, memory actions, search belief states, or long-document memory
agents. They do not appear to make the production-runtime contract
itself the central object: typed event schema, batch visibility,
non-destructive GC, replayable prompt reconstruction, and unified
handling of tool calls, artifacts, task state, and context paths.

### Recommended Claim Narrowing

Avoid these claims:

- "We introduce short-term memory for agents."
- "We are the first to compress agent context."
- "We solve long-context memory."
- "We introduce active working-context management."

Use these claims instead:

- "We introduce an event-sourced prompt materialisation contract for
  runtime short context."
- "We separate audit-log retention from prompt visibility."
- "We model tool calls, tool results, artifacts, task transitions, and
  conversation turns as typed events governed by deterministic
  visibility policies."
- "We evaluate whether event-batch materialisation improves the
  token--accuracy trade-off relative to full-context and retrieval
  baselines."

### Experimental Consequence

The experiments should include at least one ablation or diagnostic
that close papers do not naturally report:

- visibility-state distribution: number of event batches in
  `LOAD_ALL`, `LOAD_KEY`, and `NO_LOAD`;
- prompt reconstruction fidelity from the immutable event log;
- failure cases where destructive summarisation loses tool evidence
  but Structure retains the evidence in the audit log;
- token--accuracy trade-off under the same runtime trace with different
  visibility policies.

These metrics make the paper visibly different from Context-Folding
and MemAct. They evaluate the contract, not only task performance.

## Implications for Experiments

The experimental comparison should evaluate Structure on metrics that
directly reflect short-memory materialisation:

- Prompt tokens before and after event GC.
- Fraction of events kept visible.
- Number of batches in `LOAD_ALL`, `LOAD_KEY`, and `NO_LOAD`.
- Accuracy under comparable token budgets.
- Tokens per scored point.
- Failure cases where compaction loses needed tool evidence.
- Replay fidelity: whether a prompt can be reconstructed from the event
  log and materialisation policy.

For baselines, `FullText` and `NaiveRAG` remain useful, but the paper
should also describe them in short-memory terms:

- `FullText`: all available runtime/context chunks are materialised.
- `NaiveRAG`: runtime/context chunks are flattened and retrieved by
  similarity.
- `StructurePathMemory`: runtime state is routed through path entries,
  typed events, and batch visibility.

## Sources

- Claude Code memory documentation:
  https://code.claude.com/docs/en/memory
- Anthropic effective context engineering:
  https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Claude Code compaction discussion:
  https://github.com/anthropics/claude-code/issues/2714
- OpenAI Codex repository:
  https://github.com/openai/codex
- Codex `AGENTS.md` documentation:
  https://developers.openai.com/codex/guides/agents-md
- Codex context discussion:
  https://github.com/openai/codex/discussions/12668
- Codex compaction issue:
  https://github.com/openai/codex/issues/5772
- Aider repository-map documentation:
  https://aider.chat/docs/repomap.html
- Goose context-engineering documentation:
  https://goose-docs.ai/docs/guides/context-engineering/
- Cool Papers: Context-Folding:
  https://papers.cool/arxiv/2510.11967
- arXiv/OpenReview: Memory-as-Action / MemAct:
  https://arxiv.org/html/2510.12635v2
- Cool Papers: Context Gathering Decision Process:
  https://papers.cool/arxiv/2605.07042
- Cool Papers: MemAgent ICLR 2026 oral listing:
  https://papers.cool/venue/ICLR.2026?group=Oral
- Cool Papers: Experience Compression Spectrum:
  https://papers.cool/arxiv/2604.15877
- arXiv: Memory in the Age of AI Agents:
  https://arxiv.org/abs/2512.13564
