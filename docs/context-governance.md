# Context governance and local skills

Context governance controls exposure independently of Blend model routing and
existing execution permissions. Tools, MCP servers, MCP tools and skills have
independent authorization and exposure states.

## Folded and unfolded

- **Folded:** the model sees only the item's exact name and type. No description,
  tool schema, skill instructions, or resource list is supplied.
- **Unfolded:** tools use their normal callable schemas, skills supply their
  instruction text on each request, and MCP servers reveal their tool directory.
  Expanding a server does not automatically expand its child tools.

The model uses `context_unfold` with `name` and `type` (`tool`, `mcp_server`,
`mcp_tool`, or `skill`). Newly unfolded content becomes available on the next
request; calling an ordinary tool in the same response that first unfolds it
is rejected. Repeated unfolding is idempotent. Disabled items cannot unfold.
Unfolding changes exposure, never authorization or execution approval.

`context_unfold`, `context_read`, and an enabled `runtime_complete` are runtime
infrastructure. They remain unfolded so the agent can discover content and
complete its work. Explicitly excluding the context control tools is a
configuration error. Source-kind disables affect optional capabilities, not
these control tools. `runtime_complete` still requires policy authorization;
typed-completion profiles disabling it fail before the first provider call.

Initial exposure is pinned in the run snapshot. Unfolding is run-local and
recorded in history. The next run starts from the configured initial modes;
it does not automatically inherit earlier unfolding.

Without a context policy, existing tools retain their legacy unfolded behavior.
Adding local skills makes those skills folded by default, while preserving the
normal schemas for existing tools. An explicit policy defaults its optional
items to folded. Use `default_mode = "unfolded"` for normal eager exposure, or
`unfolded` to specify individual exceptions.

## Configuration

Terminal chat, one-shot mode and ACP use `$ARABICA_HOME/config.toml`:

```toml
[skills]
roots = ["skills"]

[context]
policy_id = "paper-review"
version = 1
base_sets = ["review"]
default_mode = "folded"
unfolded = ["tool:read_file"]
include = []
exclude = ["tool:write_file"]
disabled_sources = ["mcp"]

[context.sets]
review = ["tool:read_file", "tool:write_file", "skill:paper-review"]
```

This example requires a discovered skill named `paper-review`. Relative roots
resolve against `ARABICA_HOME`; absolute roots are supported. Roots may be a
single skill directory containing `SKILL.md`, or a directory whose immediate
children contain `SKILL.md`. Discovery order is deterministic. Duplicate skill
names and IDs fail validation, including duplicates across different roots.
No skill roots are scanned unless configured.

A policy selects only its base sets and explicit inclusions. Exclusion and
source-kind disable override inclusion and set membership. An empty selection
allows only context infrastructure. `unfolded` does not itself enable an item.
Unknown sets fail static validation; unknown or unavailable IDs fail run
resolution before a provider call. Flat sets support direct membership only.
All references are validated, even in unselected sets.

## Skill format and snapshots

For example, `$ARABICA_HOME/skills/paper-review/SKILL.md`:

```markdown
---
name: paper-review
description: Review the evidence and structure of a manuscript.
version: "1"
requirements: ["tool:read_file"]
resources: ["references/checklist.md"]
---
Read the manuscript and the review checklist. Identify unsupported claims,
missing comparisons and contradictions. Explain each proposed revision.
```

YAML frontmatter is optional; without a name the containing directory supplies
it. The default stable ID is `skill:<name>`; `id` can specify a different
`skill:` ID. Names must be non-empty and unique. Common extra metadata, including
`description`, is accepted without being exposed while folded. `requires` is
an alias for `requirements`.

Requirements are stable context IDs. An enabled skill with an unavailable or
disabled requirement fails run resolution. Requirements never silently grant
permissions or unfold tools. Cyclic workflow execution is not introduced:
skills are instruction bundles, not executable agents.

The host reloads skill content before each run. Runtime pins instruction and
resource text for that run, so file edits affect later runs only. Versions
include a SHA-256 hash of the full source file and declared resource contents,
with an optional author version prefix. A declared version alone cannot mask
content changes. Historical reports retain the original identity and hash;
re-executing old filesystem content is not promised.

After unfolding a skill, `context_read(name, path)` reads one of its declared
resources. Resource bodies are not eagerly inserted into the prompt. Paths
must be relative, without traversal, and remain inside the skill directory
when symlinks are resolved. Files must be UTF-8 text. Limits are 1 MiB per file,
4 MiB per skill snapshot, 16 MiB per catalog and 256 skills. Scripts can be
provided as text resources but the loader never runs them. Undeclared resources
and binary assets are not available through `context_read` in this release.

Runtime embedders can supply `SkillDefinition` values with `set_skills`, or a
host-owned `SkillSource` with `set_skill_source`. Runtime has no filesystem
loader dependency. HTTP hosts must explicitly compose their source and policy;
`arabica-server` does not load CLI user configuration.

## Tool and MCP identities

Local, host and runtime tools default to `tool:<tool-name>`. Custom tools use
`CoreRuntime::set_tools` plus a `RunnerEnvironment` implementation; hosts can
supply explicit identities with `set_context_identities`. Folding does not
require a different runner or provider implementation.

MCP tool IDs use `mcp:` followed by a JSON pair of logical server ID and original
MCP tool ID. Persistent servers use `configured:<server-name>`; ACP servers use
`session:<server-name>`. For example:

```toml
# Membership entries in a context set:
# 'mcp:["configured:docs","search"]'
# 'mcp-server:"configured:docs"'
```

MCP server IDs use `mcp-server:` followed by the JSON-encoded logical server ID.
Including a server selects its tools; individual exclusions still win. Excluding
a server denies its children. A server also becomes discoverable when a child
is selected. To unfold the server, use the exact directory name
`configured:docs` and type `mcp_server`; then unfold the advertised child name
with type `mcp_tool`. Explicitly configured unfolded children can be exposed
independently of a folded parent.

Server names are logical identifiers in this release. Renaming a server changes
its ID; changing a flattened tool display name does not change its compound ID.
Persistent and session-provided same-name servers have different identities,
so a client replacement cannot inherit the configured server's authorization.
Tool versions fingerprint input schemas, not executable implementation versions.

## Enforcement, persistence and reports

Each run records a deterministic catalog and effective policy snapshot,
including identities, versions, memberships, enablement reasons, initial modes,
and policy/catalog fingerprints. Governance-enabled calls must be authorized
and have a schema in that specific request. Enforcement precedes internal
memory/completion handling, cache reuse, permission checks and runner dispatch.
Existing permission checks continue to govern ordinary tool execution.
Context control tools operate only on authorized, host-snapshotted context.

Internal governance events are:

- `context.run.resolved`
- `context.request.exposed` (full exposure IDs and folded directory IDs)
- `context.item.unfolded`
- `context.call.started`
- `context.call.rejected`

These events contain identifiers and decisions, not prompt text, arguments,
resource bodies, credentials or endpoints. They stay out of default client
streams and short-memory/archive projection. Canonical session observers
persist them in the existing JSONL history, and restore retains their original
snapshots. Older snapshots without mode fields decode as unfolded.

Existing session history still records exact prepared model requests and tool
results, including unfolded skill content and requested resources. This feature
does not redact the original log or alter its retention.

`arabica_runtime::context_report` derives metrics from canonical history, with
series separated by identity, version and policy fingerprint. Terminal
`/context` shows folded availability, full exposure, unfolding, actual started
calls, permission denials, reuse, failures and unknown outcomes. Unfolding a
skill records exposure, not skill activation: the runtime has no reliable
signal that its workflow was followed. Missing telemetry is not zero usage.
Run success after exposure remains an association, not causal contribution.

Named ACP profile selection, desktop controls, nested sets, optional/conflicting
dependencies, non-local skill sources and controlled contribution experiments
remain future work.

## Pluggable evaluation

Context and model reports now use independent Rust evaluation strategies.
See [Evaluation strategies](evaluation-strategies.md) for `[evaluation]`
configuration, deterministic behavior clustering, and plugin registration.
