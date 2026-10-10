# Blend Tool Routing and Planning

## Summary

Keep planning as a model-callable tool. The host delegates plan generation to
the configured planning model using only context already assembled for the
current run, persists structured plan state, and exposes it through ACP. Blend
routes the next primary model call from the completed tool batch using typed
tool rules and name-based overrides.

## Implementation

- Add a canonical `plan` tool interaction kind and structured plan events; use
  the existing tool request, classification, permission, completion, and error
  lifecycle. Persist plan state so ACP can replay it after session restore.
- Add `planning_model` to Blend policy. The runtime performs one tool-free
  structured planning completion with the existing context projection, goal,
  constraints, and current plan. Validate the response before committing plan
  changes; return delegation errors to the calling model without retry loops.
- Add Blend routing rules for exact tool names, regular-expression tool names,
  and interaction kinds. Precedence is no-progress recovery, configured tool
  error route, exact name, regex name, kind, success route, then default.
- Resolve same-tier rules by descending priority and then configuration order.
  Match regexes against tool names only, validate them at config load, and treat
  unknown tools as `generic`.
- Treat all tool calls from one primary model response as one batch. Route only
  after every call reaches a terminal result. Record the matched rule, desired
  alias, effective alias, and capability fallback in route metadata.
- Keep ACP responsible for mapping persisted plan events to native plan
  updates. Keep permissions independent from the `plan` interaction kind.
- Document the configuration fields and their separation from per-session ACP
  policy selection.

## Validation

- Cover rule precedence, batches with mixed outcomes, unknown tools, invalid
  regexes, model capability fallback, and no-progress recovery.
- Cover planning delegation with existing context only, invalid output,
  cancellation, provider failure, and plan-state replay.
- Verify ACP live updates and restored sessions show the same plan state.

## Defaults

- If `planning_model` is absent, use the policy default model.
- Planning calls do not receive tool definitions and do not trigger Blend tool
  batch routing.
- A planning failure fails that tool call and leaves the previous plan intact.
- No config migration is required; omitted new settings preserve existing
  default routing behavior.
