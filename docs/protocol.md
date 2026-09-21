# Structure Command/Event Protocol

**Status:** Draft v1.0 foundation
**Source of truth:** `crates/structure-protocol`
**Transport bindings:** in-process Rust types and HTTP + SSE
**Surface status:** headless composition hosts only; web and desktop UI
implementations are intentionally absent

## 1. Purpose

The protocol is the only contract shared across Structure modules. Session
Management accepts Commands and emits ordered Events. Runtime interprets
scheduled work, calls the Model Provider, dispatches typed tool calls, and
normalizes Runner output into Events. Runner Environment executes work without
owning session or presentation state.

No module may add a private command/event vocabulary at its boundary.

A *composition host* wires the existing modules in one process and exposes this
contract without adding vocabulary of its own. `structure-server` binds it to
HTTP + SSE; `structure-cli` binds it to stdio and one-shot execution. Hosts are
not surfaces and are not blocked by the §9 checklist.

A *UI surface* renders state for a human: web, desktop, or a terminal UI. None
may be introduced until this contract is versioned, exported, and covered by
compatibility fixtures. See Appendix B of `runtime_core_architecture.md`.

## 2. Ownership

| Concern | Owner | Must not own |
|---|---|---|
| Wire types, names, version, errors | Protocol | Scheduling or execution |
| Session lifecycle, run scheduling, idempotency, sequence | Session Management | Context interpretation or runner mechanics |
| Short-memory projection, long-memory access, agent loop, normalized command output | Runtime | Event-log persistence, session identity, or UI state |
| Provider API encoding/decoding, model HTTP calls | Model Provider | Tool execution, session scheduling, or memory ownership |
| Execution, cancellation, resource policy | Runner Environment | Session/context persistence |
| HTTP request and SSE delivery | Protocol host | Domain behavior |

The dependency direction is fixed:

```text
structure-model       structure-protocol
      ↑        ↑          ↑        ↑
      │        └──────────┤        │
structure-provider  structure-runner
              ↑       ↑
          structure-runtime
                  ↑
          structure-session
                  ↑
          structure-server
```

## 3. Command Envelope

Commands express intent. They never claim that a state change already
happened.

```json
{
  "protocol_version": "1.0",
  "command_id": "client-generated-idempotency-key",
  "session_id": "session-1",
  "command": {
    "type": "message.send",
    "payload": { "content": "Explain the architecture" }
  }
}
```

Rules:

1. `command_id` is a required idempotency key.
2. Retrying the same `command_id` returns the original Events and must not
   repeat side effects.
3. `session.create` and `session.fork` omit `session_id`; all other current
   Commands require it.
4. A reused `command_id` with a different envelope is rejected as
   `invalid_command`.
5. A protocol version mismatch is rejected before dispatch.

### v1 command vocabulary

| Command | Owner | Result |
|---|---|---|
| `session.create` | Session | `session.created` |
| `session.fork` | Session | `session.forked` |
| `session.suspend` | Session | `session.suspended` |
| `session.resume` | Session | `session.resumed` |
| `session.close` | Session | `session.closed` |
| `message.send` | Session → Runtime → Model Provider / Runner | run event sequence |
| `run.cancel` | Session → Runtime → active provider and runner | `run.cancelled` |
| `context.read` | Runtime | `context.read` or `error` |
| `context.search` | Runtime | `context.search.result` |
| `context.update` | Runtime | `context.updated` |
| `context.delete` | Runtime | `context.deleted` or `error` |
| `context.set_disclosure` | Runtime | `context.disclosure.set` |

Context paths are canonical, forward-slash-separated logical paths. Leading
and trailing slashes are normalized; empty segments, control characters,
backslashes, and `.` / `..` traversal segments are rejected. Reads, searches,
and the Context snapshot supplied to Runner Environment are projected at the
Session's current disclosure level:

- `glance`: whitespace-collapsed preview, capped at 160 characters;
- `overview`: content preview, capped at 1,024 characters;
- `detail`: complete content.

Search matches against the complete stored value before projecting results.

### Short Memory and Long Memory

The two memory classes are separate by contract:

| Memory | Scope and owner | Source of truth | Lifetime |
|---|---|---|---|
| Short Memory | Session history supplied by Session Management and projected by Runtime | Immutable `EventEnvelope` log | Ephemeral per Runner request; fork inherits history only through the fork boundary |
| Long Memory | Workspace context accessed by Runtime | Workspace `LongMemoryStore` (currently an in-memory foundation adapter) | Shared across Sessions and retained after Session close |

Short Memory is never updated through `context.*` Commands. It is a pure,
deterministic projection of the Session event log. Long Memory is the target
of `context.read`, `context.search`, `context.update`, and `context.delete`.
Runtime passes them to the Model Provider in separate `short_memory` and
`long_memory` fields so no provider adapter can accidentally treat the replay
window as durable knowledge. Runner Environment receives only typed execution
requests and never owns conversational memory.

## 4. Event Envelope

Events state facts and form the replay/audit stream.

```json
{
  "protocol_version": "1.0",
  "event_id": "event-session-1-5",
  "command_id": "client-generated-idempotency-key",
  "workspace_id": "workspace-1",
  "session_id": "session-1",
  "run_id": "run-1",
  "sequence": 5,
  "occurred_at_ms": 1784967365000,
  "event": {
    "type": "command.output",
    "payload": { "stream": "stdout", "chunk": "..." }
  }
}
```

Rules:

1. `event_id` is globally unique and is the consumer deduplication key.
2. `command_id` records causation.
3. `sequence` is strictly increasing within a Session, beginning at 1.
4. All Events for one Run carry the same `workspace_id`, `session_id`, and
   `run_id`.
5. Consumers order by `sequence`, never by `occurred_at_ms`.
6. `occurred_at_ms` is informational UTC Unix time.
7. Runtime and Runner emit event payloads; only Session Management may assign
   envelopes and sequence numbers.

### v1 event families

| Family | Events |
|---|---|
| Session | `session.created`, `session.forked`, `session.suspended`, `session.resumed`, `session.closed` |
| Run | `run.scheduled`, `run.started`, `run.completed`, `run.failed`, `run.cancelled` |
| Message/output | `message.accepted`, `command.output` |
| Model exchange | `model.request.prepared`, `model.response.item`, `model.response.completed`, `model.response.rejected`, `model.response.normalized` |
| Tool call | `tool.call.requested`, `tool.call.classified`, `tool.call.reused`, `tool.call.loop_blocked`, `tool.call.completed` |
| Agent control | `agent.progress.advisory`, `agent.loop.terminated`, `terminal.control.transition` |
| Context | `context.read`, `context.search.result`, `context.updated`, `context.deleted`, `context.disclosure.set` |
| Failure | `error` |

Not every family reaches clients. `Event::is_client_visible()` decides what a
host may forward; model-exchange Events are retained as replay evidence and are
kept off client and SSE output.

Runner-specific failures are normalized by Runtime into terminal `run.failed`
Events. Ordered Runner stdout/stderr chunks are mapped one-for-one to
`command.output`; Runtime must not regroup them by stream.

Tool approval, user-input requests, task/artifact changes, and token usage
Events are deliberately not frozen yet. They must be added as typed variants;
generic untyped event escape hatches are forbidden. The Python event taxonomy
that earlier drafts planned to reconcile against was removed with the Python
implementation in `a3823ee`, so the Rust vocabulary is now the only source.

## 5. Lifecycle Rules

Session lifecycle:

```text
create → active ⇄ suspended → closed
```

Run lifecycle:

```text
pending → running → waiting_for_tool → running → finished
                  ├──────────────────────────────→ failed
                  └──────────────────────────────→ cancelled
```

Terminal Runs cannot be cancelled or resumed. Closed Sessions cannot accept
Commands. Session fork snapshots inherited Short Memory at a defined event
boundary and starts a new local sequence at 1. The fork remains bound to the
same Workspace Long Memory; Long Memory is not copied.

## 6. Delivery Semantics

- Command submission is request/response over `POST /v1/commands`.
- Every accepted Event is also published over `GET /v1/events` as the
  `structure.event` SSE type.
- Delivery is at-least-once. Consumers deduplicate with `event_id`.
- Reconnect/resume cursors and durable replay are required before v1 freeze;
  the current broadcast-only SSE binding is a development host.
- Backpressure, slow-consumer eviction, and retention are transport concerns,
  not Runtime concerns.

## 7. Error Contract

Commands rejected before a Session-scoped Event exists return
`CommandFailure`. Failures during accepted work become `run.failed` or `error`
Events. Error codes are stable machine-readable values; messages are for
operators and may change.

## 8. Compatibility Policy

- Patch releases may clarify documentation and add optional payload fields.
- Minor releases may add Commands or Events that old clients can ignore.
- Removing/renaming a type, changing required fields, or changing sequence and
  idempotency semantics requires a major protocol version.
- Dotted wire names are permanent once frozen.
- Conformance tests must cover every Command and Event wire name.
- Generated schemas and golden JSON fixtures are release artifacts, not UI
  source code.

## 9. Freeze Checklist

- [x] Explicit module ownership and dependency direction
- [x] Versioned Command/Event envelopes
- [x] Dotted canonical wire names
- [x] Session-scoped ordering metadata
- [x] Command idempotency in the in-memory scheduler
- [x] HTTP command and SSE event bindings
- [x] Wire-name conformance tests
- [x] JSON Schema export at `GET /v1/schema`
- [ ] Durable command deduplication and event replay
- [ ] Asynchronous `CommandReceipt` acceptance and background run dispatch
- [ ] Reconnect cursor semantics
- [x] ~~Python event-taxonomy reconciliation~~ — dropped; the Python
      implementation was removed in `a3823ee`
- [ ] Typed tool approval and user-input flow
- [ ] OpenAPI transport document
- [ ] Golden cross-language fixtures
- [ ] Load/backpressure and multi-session scheduling tests

Web and desktop UI surfaces remain blocked by this checklist by design.
Headless composition hosts are not blocked: they add no vocabulary and bind the
same contract the checklist protects. See §1 and Appendix B of
`runtime_core_architecture.md`.
