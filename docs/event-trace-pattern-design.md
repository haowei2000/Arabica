# Unified event trace and pattern design

Status: V1 projection and Blend matcher implemented; the remaining sections describe the target design.

Implemented: all canonical event types produce newline-delimited records, sorted scalar fields, percent-encoded values, event identity and sequence, run/call correlation, conservative shell executable labels, and a one MiB trace bound. `event_trace_v1_regex` matches the preceding model-step window within the current run, including windows without completed tools. Legacy `event_trace_regex` remains unchanged.

Pending: structured TraceRecordV1 public API, selectable scopes/windows, permission integration, preview/replay UI, compiled matcher caching, and the complete alias catalog. Current permission events retain canonical paths with a `call` field.

This specification replaces the partial, ad hoc Blend trace representation with a total, versioned projection of canonical events. It preserves structured events and session sequencing as the source of truth. A regular expression matches a derived trace; it never becomes the persistence format.

## 1. Goals and current limitations

The current protocol contains 44 event variants. The current `tool_event_trace` emits only selected model, tool, permission, and agent events, uses Debug formatting for some values, and recognizes shell commands with `split_whitespace`. Its trace covers the preceding model step and is evaluated inside the completed-tool routing loop.

The new format must cover every event, combine tool and trajectory matching, preserve event identity and order, distinguish absent metadata from successful results, and support deterministic replay. It must also allow the readable path `toolcall.shell.sed` without treating a shell command prefix as proof that a command is safe.

## 2. Three layers

1. **Canonical facts:** typed `EventEnvelope`, unchanged. Events retain their IDs, session sequence, run IDs, call IDs, and typed payloads.
2. **Normalized records:** `TraceRecordV1` contains the canonical type, a display/match path, allowlisted scalar fields, source identity, and a projection version. Derived values have documented provenance.
3. **Match views:** a deterministic serializer produces regex input from normalized records selected by an explicit scope and window. Model routing, permission rules, diagnostics, and replay share the normalizer, while retaining separate actions and evaluation times.

Crate ownership: protocol owns record/version types and the event catalog; session owns ordered projection and replay; runtime owns rule evaluation and actions; runner supplies typed tool/command metadata; CLI owns TOML parsing; desktop displays explanations. Matching must not depend on Swift UI state or provider-specific response shapes.

## 3. Canonical text grammar

Each record is one newline-terminated UTF-8 line:

```text
record = path *("|" key "=" value) "\n"
path   = atom *("." atom)
key    = atom *("." atom)
atom   = 1*(ALPHA / DIGIT / "_" / "-" / pct-encoded)
value  = *(ALPHA / DIGIT / "_" / "-" / pct-encoded)
```

The path is first. Attributes are sorted by ASCII key; no whitespace surrounds delimiters. Values use uppercase UTF-8 percent encoding. Dots, pipes, equals signs, percent signs, whitespace, slashes, and line breaks in dynamic values are encoded. Fixed path/field names and enum vocabulary are lowercase; dynamic strings preserve case. Integers use base ten; booleans use `true` or `false`. Missing fields are omitted; an empty string remains `key=`; null uses an explicit presence/type field when a rule needs to distinguish it.

Example records, with illustrative local call handles:

```text
toolcall.shell.sed|call=c0001|command.kind=simple|tool=shell|type=tool%2Ecall%2Erequested
toolpermission.shell.sed.resolved|call=c0001|outcome=allowed|scope=once|type=tool%2Ecall%2Epermission_resolved
toolresult.shell.sed.success|call=c0001|outcome=success|tool=shell|type=tool%2Ecall%2Ecompleted
```

Source event IDs and session sequence numbers are retained in the structured records and match evidence. Local `c0001` handles are deterministic within the run, assigned in request order, and do not replace original call IDs. Human-facing previews may decode values, but regex input always uses the canonical encoding.

`trace_version = 1` is required in rules; it is not an extra event line. Normalization never fabricates a successful event. A missing request produces an explicit unresolved association rather than guessing a tool or command.

## 4. Event path vocabulary

Every event retains its exact protocol type as an attribute. Convenience paths are registered aliases with a single documented meaning.

- `tool.call.requested` becomes `toolcall.<tool>[.<command>]`.
- `tool.call.completed` becomes `toolresult.<tool>[.<command>].success` or `.error`.
- `tool.call.classified` becomes `toolclass.<tool>[.<command>].<kind>`.
- Permission events become `toolpermission.<tool>[.<command>].requested` or `.resolved`; the resolved outcome is an attribute.
- Reuse and loop-block events become `toolreuse.<tool>[.<command>]` and `toolblocked.<tool>[.<command>]`.
- Other events use their existing canonical dotted event name as the path. They may expose stable enums through attributes, not arbitrary prose appended to the path.

The following complete catalog is projected; fields listed are examples of allowlisted match metadata, not the full persisted payload:

### Lifecycle

- `session.created`: workspace identity.
- `session.forked`: source-session identity.
- `session.resumed`, `session.suspended`, `session.closed`: lifecycle transition.
- `run.scheduled`, `run.started`: run identity.
- `run.completed`: output presence, without output content.
- `run.failed`: stable failure code/category where available; no inference from message text.
- `run.cancelled`: cancellation fact.
- `message.accepted`: content presence, without message text.
- `error`: typed error code.

### Model and routing

- `model.route.selected`: step, alias, policy identity/version, route reason.
- `model.route.explained`: desired/selected aliases, rule ID, fallback category.
- `model.call.observed`: step, typed outcome, elapsed milliseconds, usage counters.
- `model.request.prepared`: step, model identity, tool count; no prompt or authorization data.
- `model.response.item`: step, item index, typed item kind; no text/reasoning content.
- `model.response.completed`: step, typed finish reason, usage counters.
- `model.response.rejected`: step, rejection reason, tool count.
- `model.response.normalized`: step, controller policy, normalization flags.

### Tools and permissions

- `tool.call.requested`: tool, call association, approved argument selectors, command classification.
- `tool.call.classified`: tool, call association, interaction kind.
- `tool.call.permission_requested`: linked tool/call.
- `tool.call.permission_resolved`: linked tool/call, outcome, scope, source.
- `tool.call.reused`: linked tool/call, source-call association, repeat count.
- `tool.call.loop_blocked`: linked tool/call, repeat count.
- `tool.call.completed`: tool/call, success/error; no result text.
- `tool.execution.observed`: call association, actual runner duration and outcome.
- `command.output`: stream and content presence; no output content. Do not invent a call association when the canonical event has none.

### Context, planning, and control

- `context.run.resolved`: policy identity/version, selected/excluded counts.
- `context.request.exposed`: step, decision identity, unfolded/folded counts.
- `context.item.unfolded`: call, decision, context identity.
- `context.call.started`, `context.call.rejected`: call, decision, context identity if available.
- `context.read`, `context.updated`: stable entry metadata.
- `context.search.result`: entry count.
- `context.deleted`: allowlisted path metadata.
- `context.disclosure.set`: typed disclosure level.
- `plan.updated`: plan identity and status counts.
- `plan.delegation.observed`: step, model alias, success flag, elapsed time, usage.
- `agent.progress.advisory`: step, consecutive no-progress count; no prose.
- `agent.loop.terminated`: step, typed termination reason, no-progress count.
- `terminal.control.transition`: step, policy, from/to states, reason.

Normalization uses an exhaustive Rust match over `Event`; adding an event requires a catalog entry. Imported unknown event types use a marked unknown record if the persistence reader supports opaque events; they are never silently dropped. Current typed readers that reject unknown variants still report that compatibility error.

## 5. Tool parameters and shell commands

Argument matching remains typed in the normalized record. Approved selectors use JSON Pointer, for example `/command`, `/options/path`, or `/files/0`. A rule may apply `value_regex` to the original scalar string independently of percent encoding. Objects/arrays require an explicit canonical JSON projection rather than an implicit conversion.

For `toolcall.shell.sed`, `sed` is a **derived command label**, not the complete command. Obtain it from syntax-aware, non-executing shell parsing. A supported simple literal executable can be identified even when quoted. Assignments, `env`, wrappers, substitutions, pipelines, chains, and scripts need explicit handling. A compound or unresolved command has `command.kind=compound` or `unknown` and must not be represented as a trusted single `sed` command. Store each supported executable separately as structured metadata if compound-command matching is required.

Commands such as `sed file; rm file` must never qualify for a permission rule intended to allow only a simple `sed` invocation. No shell evaluation, executable launch, or environment expansion is performed during normalization. Trace matching can select a model, but cannot prove execution safety.

Keep credentials, raw prompts, reasoning, tool output, and arbitrary user text out of default regex input. Sensitive parameter selectors are opt-in and local to the matcher; explanations contain field paths and match positions, not raw values. Regex-validation errors must not print configuration excerpts that could contain secret patterns.

## 6. Scope, window, and correlation

Rules explicitly declare their scope:

- `step`: current run, events since its preceding model-route decision. This is the default Blend view.
- `run`: the current run only, optionally bounded to its last N completed steps.
- `tool_call`: records belonging to one call, enriched from that call's request. Use this for request/permission/result chains that must refer to the same invocation.
- `session`: opt-in cross-run matching, with explicit synthetic `trace.boundary.run` records between runs. Synthetic records are marked and versioned separately from facts.

Order always follows session sequence, never timestamps or completion arrival in worker tasks. Routing evaluates the snapshot before its own new decision event is appended, so its decision cannot trigger itself in that evaluation. Lifecycle-only and session events remain searchable even though they are not actionable at every route boundary.

A run-level regex matching a request and result cannot by itself prove that both belong to one call. Rust regex has no backreferences. Use `tool_call` scope or typed correlation constraints; do not infer equality from matching names. Future parallel tools remain ordered canonical events with explicit call association; do not reorder facts into artificial request/result pairs for matching.

Use canonical audit events or an equivalent maintained trace index, not model-facing short-memory history. Memory projection/GC must not erase routing evidence. If requested history is unavailable, return an explicit incomplete-trace outcome. Never silently truncate and interpret a partial trace as complete.

## 7. Regex semantics and unified rule format

Use Rust `regex` syntax, compiled once when loading a policy. Matching is a search by default; `^`/`$`, `(?m)`, and `(?s)` have ordinary regex meanings. Dot in a path is literal and must be escaped. Use TOML literal strings to avoid doubled escapes.

A single rule list supports path/sequence regex plus optional typed argument predicates. A tool-only regex is a one-event path match; a trajectory regex spans several record lines. There is no separate matcher-type priority tier in this new format.

Proposed configuration:

```toml
[blend.policies.coding]
version = 2
trace_version = 1
default_model = "fast"

[[blend.policies.coding.rules]]
id = "sed-success"
priority = 20
scope = "tool_call"
trigger = "before_model_call"
pattern = '(?m)^toolcall\.shell\.sed\|[^\n]*\n(?:[^\n]*\n)*toolresult\.shell\.sed\.success\|'
model = "fast"

[[blend.policies.coding.rules]]
id = "tool-error"
priority = 10
scope = "step"
trigger = "before_model_call"
pattern = '(?m)^toolresult\.[^|\n]+\.error\|'
model = "strong"

[[blend.policies.coding.rules]]
id = "shell-read-command"
priority = 5
scope = "tool_call"
trigger = "before_model_call"
pattern = '(?m)^toolcall\.shell\.[^|\n]+\|'
model = "fast"

[[blend.policies.coding.rules.parameters]]
selector = "/command"
value_regex = '^sed[[:space:]]+-n[[:space:]]+[0-9,]+p[[:space:]]+'
```

All parameter predicates must match the associated request. Missing/null selectors are non-matches unless explicitly requested. For permission actions, classify a pending request before execution; a pattern cannot depend on that call's future result. For routing, patterns consume only committed preceding events.

Sort matching rules by descending `priority`, then declaration order. First eligible rule wins; no match uses the default model. Validate duplicate IDs, unknown scopes/triggers, invalid regex, undefined aliases, and incompatible action/scope combinations on load. Capability eligibility is checked after selection; record desired and effective models separately. Policies are pinned per run.

Legacy recovery/error/tool/default precedence remains unchanged for legacy policies. Compile legacy configurations into explicitly prioritized internal rules only after parity is proven. Do not merge legacy implicit priorities with the new list silently. The policy selects either legacy semantics or unified-list semantics. Planning delegation remains a distinct action with its own trigger.

## 8. Limits, evidence, and replay

Bound regex size, compiled automaton size, trace bytes, events, and retained windows. Choose measured defaults during implementation. Disallow unbounded session-history matching in online routing. On an incomplete projection or limit breach, return a typed diagnostic and an explicit configured fallback; permission matching fails closed. No backtracking regex engine is required.

Each decision records trace version, policy fingerprint, scope/window, source sequence span, completeness, winning rule ID, matched event IDs and character spans, desired/effective model, and fallback category. A preview shows the same encoded input and highlights matched records. This allows the desktop to explain a route and replay it without provider calls.

Record normalization is a public compatibility contract separate from transport protocol versions. Changing a path, field encoding, alias, or window meaning requires a new trace version. Additive fields can affect broad regexes, so the v1 field set is frozen; extensions require explicit projection selection or a new version.

## 9. Implementation and migration

1. Add the exhaustive catalog, normalized record type, deterministic encoder, and versioned fixtures.
2. Add safe tool metadata and syntax-aware shell labeling; keep unresolved commands explicit.
3. Build ordered session trace views and correlation indexes independent of short-memory projection.
4. Introduce the unified rule list, precompiled regex, typed parameter predicates, limits, and decision evidence.
5. Add a desktop rule preview/replay view using the exact matcher input.
6. Retain the current `regex_name` and partial `event_trace_regex` as legacy behavior. Require explicit opt-in to v1; do not reinterpret existing patterns.

Verification must cover all 44 event variants, delimiter/unicode round trips, deterministic ordering, repeated calls with identical names, parallel interleaving, cross-run boundaries, missing requests, unavailable history, compound shell commands, rule precedence, capability fallback, and legacy replay parity. Test fixtures must contain synthetic content only.
