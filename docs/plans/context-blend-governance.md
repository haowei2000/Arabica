# Context Blend Governance

## Goal

Give Arabica policies explicit control over the non-model context supplied to
an agent: built-in and runner tools, MCP servers and their tools, and skills.
For every context item, make it possible to answer:

1. Was it available to the model, selected or invoked, and what happened next?
2. Which group, dependency, or policy relationship caused it to be included?
3. Which policy enabled or disabled it for this run?

Context governance complements model routing. Blend continues to choose the
model for a model call; context policy decides which tools, MCP capabilities,
and skill instructions that call can see and use.

## Current state and boundaries

- `ToolInteractionKind` classifies runner calls for memory and routing. It is
  not a stable identity or lifecycle for every context source.
- ACP sessions can attach MCP servers, and `arabica-cli/src/mcp.rs` namespaces
  their exposed tools. Server configuration is currently session-scoped.
- The repository has no first-class skill catalog, loader, or ACP lifecycle.
  Skill support must begin with a source and identity contract rather than
  assuming that skills are already runtime objects.
- Current Blend policy config controls model aliases and tool-call routes. It
  does not select tools, MCP servers, or skills.
- The session log is the canonical audit history. Do not create a second
  mutable event store for context metrics.

This plan does not change permission semantics. Context enablement controls
whether a capability is offered to a model; existing permission checks still
decide whether an offered call may execute.

## Context identity and lifecycle

Introduce a provider-neutral context identity with a source kind, stable ID,
optional version, and display name. Names are for presentation; policy matches
stable IDs.

- **Tool:** runner or host tool ID, with an optional interaction kind.
- **MCP server:** configured server ID. Its tools use a compound identity of
  server ID and tool ID, even when ACP requires a flattened exposed name.
- **Skill:** stable skill ID and version, plus a declared set of tool/MCP
  requirements. Skill loading is a later source integration because there is
  no current skill runtime.

Context lifecycle distinguishes discovery, policy selection, request
exposure, activation, execution outcome, and run outcome. A tool being
advertised to a model is exposure; a completed tool call is activation. A
skill being included in a prompt is exposure; a recorded skill invocation or
matched skill workflow is activation. Preserve these distinctions in events
and reports.

Every run snapshots the resolved context catalog and effective policy version
before the first model call. Mid-run edits apply to future runs only. Events
carry stable IDs, versions, policy ID/version, and a decision ID, never full
prompt text, secret values, or unredacted tool arguments.

## Management relationships

Represent reusable context organization as named **context sets**. A set can
contain tools, MCP servers or individual MCP tools, and skills. Sets may
include other sets and declare required, optional, or conflicting items.

- Resolve set membership into a deterministic, deduplicated context list.
- Reject unknown IDs, dependency cycles, and unsatisfied required items when
  loading configuration.
- Keep the relationship graph separate from model aliases and Blend route
  rules.
- Record the resolved membership in the run snapshot so reports remain
  interpretable after catalog changes.

The first slice should support simple named sets and direct membership. Add
nested sets, dependencies, and conflicts only after the simple model is
validated against real tool/MCP/skill catalogs.

## Policy controls and precedence

Add context controls to named Blend policies, independent of model-routing
fields:

- Select one or more context sets as the policy's base context.
- Enable or disable source kinds: tools, MCP, and skills.
- Apply explicit include and exclude rules by stable context ID.
- Allow a session to select an advertised context profile through an ACP
  session config option; keep session mode reserved for modes supplied by the
  host.

Resolve controls in this order:

1. Host safety and permission restrictions.
2. Policy explicit exclusions.
3. Session-level exclusions.
4. Policy base sets and explicit inclusions.
5. Session-level enablement among the options the policy makes available.
6. Explicit denials always win over inclusions.

The final tool schemas and skill content are filtered before constructing the
model request. The runner also checks that a model-selected call is enabled
for the run; hidden UI controls alone are not enforcement. An unavailable or
disabled context item is rejected with a stable, non-secret diagnostic.

Persistent catalog and policy definitions belong in `config.toml` and the
settings form. The selected context profile for one conversation belongs in
ACP session config options and is shown in the composer. Each run records the
resolved choice.

## Measuring contribution

Collect evidence at three levels, with an explicit distinction between
association and causal contribution.

### Exposure

- Whether the item was resolved, enabled, and included in a request.
- Number of model calls and runs where it was available.
- Estimated token contribution for skill text and tool schemas when the
  provider/request encoder can measure it reliably.

### Activation

- Tool/MCP call count, permission outcome, execution outcome, retries, and
  latency.
- Skill exposure and activation signals when the skill integration can
  provide a reliable signal; do not infer activation from prompt inclusion.
- Policy decision that enabled or disabled each item.

### Downstream outcomes

- Run completion status, cancellation, and failure category after exposure or
  activation.
- Tool outcomes associated with the next model call and run, clearly labeled
  as associations rather than causal effects.
- Later offline comparisons for contribution estimates, using matched tasks
  or controlled ablations; never claim causal value from raw success rates.

Do not rank a context item as useful from invocation count alone. Reports must
show denominators (eligible, exposed, activated runs), unknown outcomes, and
sample size. Keep raw prompt bodies and sensitive arguments out of telemetry.

## Event and report shape

Extend the existing internal event history with typed events or typed
decision metadata for:

- Catalog discovery and a stable catalog fingerprint.
- Effective policy/profile selection and resolved membership at run start.
- Per-request exposure IDs and source versions.
- Per-item activation and terminal result, linked to the tool-call ID where
  applicable.
- Run outcome and the effective context policy fingerprint.

Keep high-cardinality catalog data out of client-visible event streams unless
the ACP client explicitly requests it. Reports should be reproducible from
the canonical event history and should clearly separate missing telemetry
from zero usage.

## Implementation sequence

### Phase 1: Inventory and observability contract

- Audit runner tools, ACP-provided MCP servers, host tools, and the expected
  skill source.
- Define stable IDs, versioning, provenance, lifecycle states, and redaction
  rules.
- Add typed internal exposure and activation records and a report projection.
- Establish baseline counts and attribution limits before policy filtering is
  introduced.

### Phase 2: Catalog and relationship model

- Add the context catalog and named context sets.
- Resolve MCP server and tool identities without relying on display names.
- Validate references, duplicates, cycles, and required/conflicting items.
- Add unit and serialization tests for deterministic catalog snapshots.

### Phase 3: Policy enforcement

- Add persistent context set, enable/disable, include, and exclude fields to
  named Blend policies.
- Apply the resolved policy to tool definitions and skill content before each
  model request.
- Reject disabled tool calls in the runtime/runner boundary and record the
  decision.
- Keep tool permissions, user approval, and Blend model routing independent.

### Phase 4: ACP and desktop controls

- Advertise available context profiles as ACP session config options.
- Add a composer control for the current conversation's profile.
- Update the settings form to edit persistent context sets and policy
  enablement in the same hierarchy as `config.toml`.
- Show effective context and policy in diagnostics without exposing secrets.

### Phase 5: Evaluation and skill integration

- Add reports for exposure, activation, outcomes, and attribution confidence.
- Integrate a skill source only after its discovery, version, and activation
  signals are specified.
- Run controlled ablations on representative tasks before proposing any
  automatic context promotion or removal.

## Validation and acceptance criteria

- A run snapshot identifies every available, enabled, exposed, and activated
  item by stable ID and version.
- Switching a context profile changes the model-visible tools and skill
  content for new runs and cannot bypass host permission checks.
- Explicit policy exclusions remain effective against set membership,
  session selections, and attempted direct tool calls.
- MCP tool identity remains stable across display-name changes and collisions
  are rejected deterministically.
- Context metrics can distinguish not exposed, exposed but unused, used with
  success, used with failure, denied, and unknown.
- Reports include denominators and do not label observational associations as
  causal contribution.
- Session restore reproduces the policy/profile snapshot used by the run.
- Config validation, ACP conformance, runtime tests, and desktop tests cover
  the full lifecycle.

## Decisions to make before implementation

- Where skills come from and whether a skill is host-loaded text, an ACP
  capability, a tool bundle, or a combination.
- Whether context sets may nest in the first release.
- Which event data is retained locally and for how long.
- Whether profiles are selected per session only or may also vary by run.
- How MCP server config is divided between persistent user settings and
  session-provided servers.

