# Run evaluation and policy comparison

Structure evaluates persisted facts using its Rust runtime, detached evaluation
worker, and SQLite adapter. No parallel agent runtime or Python evaluator is
required. Ordinary run completion never implies verified task success.

## Per-run observations

A terminal checkpoint produces an immutable snapshot for every finished run in
that session. Runs are keyed by workspace, session, and run ID, and stop at the
first terminal event. Later turns cannot change their evidence.

The snapshot records queue time, wall time, approval-wait time, active time,
first recorded model-response item, summed model-call durations, summed runner
invocation durations, tool counts, incomplete tool calls, and reported input
and output tokens. Approval waits use interval union, including unresolved
waits through cancellation. Active time is wall time minus this union. Model
and runner sums are resource durations, not a decomposition of parallel wall
time. Clock regressions make timestamp-derived durations unavailable.

`tool.execution.observed` is an additive protocol event measuring an actual
runner invocation with a monotonic clock. It excludes permission waits and is
recorded for successful, failed, and cancelled runner invocations. A successful
runner invocation can still return an errored tool result. Internal runtime
tools and reused/denied calls do not receive a runner duration. Old histories
therefore retain unavailable runner duration, rather than an invented zero.

First response is the first persisted `model.response.item`; it is not a
measurement of the desktop's streamed first token. Token counts retain
`usage_reported_calls / model_calls` coverage. Monetary cost is not estimated
without a versioned pricing source.

## Acceptance and scorer interface

`RunAcceptanceScorer` is registered in `EvaluationRegistry` independently of
context and model observational evaluators. The built-in `evidence-checks` scorer
supports `output_contains`, `output_regex`, `tool_succeeded`, and
`artifact_contains`. Custom Rust scorers implement the same trait. No LLM judge
or external test-command executor is enabled by default.

Each criterion produces pass, fail, or unknown; the shared status type also
reserves not-applicable for custom scorers. Completion is the passed fraction
of evaluated criteria, and coverage is the evaluated fraction of all criteria.
Both are expressed as basis points (10,000 = 100%). All built-in criteria have
equal weight. Unknown evidence is not treated as passed. Verified success is
true only for a completed run with a non-empty specification whose criteria
all pass. A failed/cancelled run or failed criterion is false; missing evidence
on an otherwise completed run leaves success unknown.

A successful `tool_succeeded` criterion is evidence of a tool outcome, not proof
of semantic artifact correctness. Output markers and file substrings likewise
prove only their declared checks. Use a domain-specific scorer for stronger
validation. Specifications must be fixed across candidates, not tailored to
one candidate's answer.

The bounded snapshot excludes prompts, command arguments, provider envelopes,
and raw tool output. It may contain up to 64 KiB of final answer plus a separate
64 KiB artifact budget. Oversized answers are omitted and output checks become
unknown. Artifact capture is explicitly selected by benchmark criteria and
happens immediately after termination, before another trial. Missing files fail
artifact checks; unreadable, oversized, non-UTF-8, or symlinked files are unknown.
The normal chat worker never reads live workspace files in the background.

Snapshots, scorer identity/version/configuration, specification fingerprints,
and results are stored in `evaluation.sqlite3`. Schema 1 migrates to schema 2
without deleting old reports. Querying an old report remains compatible.
Refreshing reuses frozen artifact evidence rather than reading files again.
The local database contains user artifacts and uses owner-only creation
permissions; the benchmark output directory is private on Unix.

## Inspect or score existing runs

```bash
arabica evaluations runs SESSION_ID --json
arabica evaluations score SESSION_ID RUN_ID --specification acceptance.json --json
arabica evaluations show SESSION_ID --json
```

An explicit acceptance specification:

```json
{
  "task_id": "explain-routing",
  "version": 1,
  "criteria": [
    {"id": "required-term", "check": {"type": "output_contains", "value": "event_trace_v1_regex"}}
  ]
}
```

Without a specification, automatic reports contain timing and evidence with
unverified acceptance. Scoring appends a versioned result; new criteria do not
rewrite the terminal snapshot. The desktop evaluation panel shows execution
status, acceptance, completion, coverage, and timing. Refresh after offline
scoring to update the desktop's worker cache.

## Run real candidate campaigns

The runner lives in the existing Rust benchmark package and composes the
production CLI host, provider catalog, runner, session manager, and persistence.
Configure at least two Blend policies with certified tool-capable aliases in
`config.toml`. Candidate entries can select a configured `policy_id` or add a
`version` to select a persisted candidate from the evaluation database.

```bash
cargo run -p structure-short-memory-benchmark --bin policy_campaign -- \
  --manifest benchmarks/runtime-short-memory/fixtures/policy-campaign.example.json \
  --output /tmp/arabica-policy-campaign-001
```

The output directory must not exist. This command makes real provider calls.
It bounds model steps and task time, uses a fresh directory/session/archive for
each trial, initializes identical fixtures, rotates candidate order between
blocks, and captures requested files before moving on. MCP connections are not
loaded for this local-tool benchmark. Shell is available only with an explicit
`--allow-shell` flag. Cancellation uses the production runtime's cancellation
path. A crash/configuration failure stops the campaign with earlier trial
results preserved; failed terminal tasks remain measured trials.

Outputs include `trials.json`, `comparison.json`, and `store/` containing private
session and evaluation databases. Results include success rate, scored sample
count, total-time median/P95, active-time median, token totals and usage coverage.
They do not automatically activate a winning policy or assert statistical
significance. One small example campaign is a smoke workload, not sufficient
training or validation data.

## Compare previously scored trials

```bash
arabica evaluations compare --manifest comparison-manifest.json --json
```

```json
{
  "trials": [
    {"candidate":"baseline", "repetition":0, "workspace_id":"WORKSPACE_A", "session_id":"SESSION_A", "run_id":"RUN_A"},
    {"candidate":"candidate", "repetition":0, "workspace_id":"WORKSPACE_B", "session_id":"SESSION_B", "run_id":"RUN_B"}
  ]
}
```

Point `ARABICA_HOME` at a campaign's `store/` to query its sessions. Comparison
requires at least two candidates, identical task/input/specification/scorer/repetition
sets, a consistent model registry across arms, a single effective policy per
candidate, and no duplicate runs. Campaign fixture fingerprints also bind initial files,
model-step/time limits, and shell availability. Ordinary historical comparisons
require the operator to control initial workspace state, which cannot be
reconstructed from a prompt hash. Scores must reference the exact snapshot.
Unknown acceptance remains visible in scored-run coverage. Comparison reports
are persisted for audit. These are actual observations, not counterfactual
predictions obtained by replaying a different policy over one history.
