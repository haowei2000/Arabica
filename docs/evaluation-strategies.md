# Evaluation strategies

Context and model evaluation are independent, read-only Rust plugin boundaries.
They consume the same canonical event evidence and never change context grants,
folding state, or model routing. The CLI `/context` command runs both strategies.

Configure them alongside `[context]`, `[skills]`, and `[blend]` in
`$ARABICA_HOME/config.toml`:

```toml
[evaluation]
context_strategy = "behavior-clusters"
model_strategy = "observational"
max_clusters = 3
min_cluster_runs = 12
```

Both strategies default to `observational`. Unknown strategy IDs fail validation.
`max_clusters` must be between 1 and 16; `min_cluster_runs` must be at least
`max_clusters`. Evaluation configuration is independent of the routing policy.

## Built-in strategies

`observational` preserves existing context exposure, activation, permission,
reuse, and outcome counts. Model evaluation reports call observations, elapsed
time, token usage, provider failures, and downstream tool outcome associations.
Model aliases are evaluated separately for each routing policy fingerprint and
model registry fingerprint. Runs with missing registry/policy metadata or a
registry/policy change within a run are excluded from model cohorts and counted
in diagnostics.

`behavior-clusters` partitions runs using three features: unique model steps,
ordinary tool requests, and distinct unfolded context items. Context control
calls and `runtime_complete` are excluded from ordinary tool counts. Counts are
transformed with log(1 + count) and standardized, then grouped with deterministic
farthest-point initialization and at most 32 Lloyd iterations. Stable run order
makes repeated grouping reproducible for the same dataset. Identical points may
produce fewer groups than `max_clusters`.

Outcomes, latency, tokens, model identity, and prompt content do not enter cluster
features. Runs without model route evidence form an `unknown-behavior` group.
Insufficient samples produce an `unclustered` group and a diagnostic. Model
registry/policy cohorts are applied within each behavior group. Group identifiers
are local to a dataset; they are neither persistent task types nor quality ranks.
The CLI currently evaluates the selected session; Rust callers can supply
histories spanning multiple sessions, with run IDs namespaced by workspace and
session to prevent accidental joins.

Behavior clustering describes execution patterns, not task semantics or task
correctness. Tool and model choices themselves affect execution patterns, so
these cohorts are not randomized or causal comparisons. A future semantic
strategy should explicitly define its task labels or embedding source, missing
values, sample limits, and evaluation criteria. Neither built-in produces a
causal usefulness score or automatically optimizes policy.

## Registering a Rust strategy

Implement `ContextEvaluationStrategy` or `ModelEvaluationStrategy` and register
it through `EvaluationRegistry::register_context` or `register_model`. Each
strategy declares an ID, a version, and a configuration fingerprint, and returns
typed groups and diagnostics. Duplicate IDs are rejected independently in each
registry. Hosts select a registered strategy by ID; the stock CLI registers the
two built-ins. Custom host composition can register additional compiled plugins.
There is no dynamic library or arbitrary script loader.

`EvaluationEvidence::from_history` removes prompts, model requests/responses,
tool arguments/results, provider state, and failure/reason text before dispatch.
Plugins receive structural IDs, public context/model identities, policy and
catalog snapshots, permission outcomes, usage, timings, and run outcomes. This
is data minimization, not a process sandbox: compiled plugins execute in the
host process. Result envelopes record evidence schema version and fingerprint,
plus plugin identity, version, and configuration fingerprint for auditability.

## Asynchronous isolation and SQLite persistence

Evaluation and Agent execution must remain asynchronously isolated. This is a
host composition invariant: runtime, provider calls, tool execution, and session
sequencing must never await evaluation, acquire its database connection, or
apply its result as a routing/authorization change. Regression tests block the
evaluator and fill its queue while observing run events to enforce this boundary.

The CLI hosts (one-shot, interactive, terminal UI, and ACP) append canonical
history to JSONL as before. After a terminal run event, an evaluation observer
only attempts `try_send` of a workspace/session/sequence checkpoint to a bounded
64-entry queue. It neither copies the prompt history nor reads config or SQLite
in the event callback. Queue overflow or a disconnected worker increments a
separate dropped-notification counter and does not affect the Agent result.
Evaluation configuration validation also happens in the background; an invalid
`[evaluation]` section does not fail runtime construction.

A dedicated OS thread per `ARABICA_HOME` reads persisted history through the
checkpoint, loads evaluation config, sanitizes evidence, runs the two strategies,
and writes `$ARABICA_HOME/evaluation.sqlite3`. This is separate from Tokio's
runtime worker threads. Database errors, plugin errors, and caught evaluator
panics increment evaluation failure counters; the Agent does not wait for
retries or worker shutdown. SQLite uses WAL with a bounded 250 ms lock timeout,
and commits the strategy snapshots, policy decision snapshots, and paired
context/model reports atomically. Repeated checkpoints are idempotent; cached
reports never regress to an older event sequence.

SQLite stores these versioned projection tables:

- `evaluation_strategies`: evaluator kind, ID, version, configuration fingerprint
  and evaluation configuration JSON.
- `evaluation_policies`: context decision snapshots and model routing policy
  identities/fingerprints plus public model registry snapshots. Historical model
  events do not contain the full routing rule definitions, so these rows cannot
  reconstruct those rules.
- `evaluation_results`: workspace/session checkpoint, evidence fingerprint,
  evaluator identities/versions/configuration fingerprints, complete evaluation
  config, typed paired report JSON, and insertion time.

`PRAGMA user_version` identifies schema version 1. Unsupported versions are
rejected before schema changes. This database contains derived data and is not
used for restoring or driving an Agent session. JSONL remains the source of
truth; prompts and tool bodies are not duplicated into this database.

At worker startup, persisted sessions are scanned and their latest terminal
checkpoints are replayed. This recovers missed notifications from queue overflow,
process exit, or earlier database failures. It reevaluates historical evidence
under the currently selected evaluation config and saves that config with the
result; it does not claim that this evaluator was active during the original
run. There is no guarantee that evaluation commits before a one-shot process
exits. Startup replay or an explicit `/context` request provides recovery.

`/context` requests a background refresh and displays the last completed report
from a nonblocking in-memory cache, including its event checkpoint and failure/
drop counters. The cached report can be stale; the command never invokes the
evaluation strategies synchronously. Its existing history and short-memory
preview are independent diagnostics. The HTTP host currently has no JSONL
persistence wiring and does not automatically schedule these projections; custom
hosts can compose `EvaluationWorker` through `SessionEventObserver` after their
canonical file store. `EvaluationWorker::start_with_registry` also accepts a
registry factory for compiled custom strategies; the factory and strategy
execution both run on the evaluation thread.

Structured CLI queries, optional ACP evaluation extensions, and the read-only
HTTP bridge are documented in [Client evaluation queries](client-evaluation.md).
