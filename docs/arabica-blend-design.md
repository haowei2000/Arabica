# Arabica Blend: Adaptive Model Routing Design

Status: active implementation, 2026-09-29. The measurement baseline and static
multi-model routing are implemented; the first routing correctness refinement
is in progress. Related papers and source links are in
[the reading list](blend-reading-list.md).

## Implemented slice

The current branch records an internal `model.route.selected` event before
each provider call and a `model.call.observed` event after success or failure.
These events include a stable decision ID, policy identity/version, selected
alias (or model ID for the legacy single-model path), elapsed time, provider
success, and reported token usage. They stay out of client event streams and
short-memory projections. `evaluate_blend_history` rebuilds per-model call and
downstream tool metrics plus per-policy terminal run counts from canonical
history. Tool outcomes in the per-model report are associations with subsequent
calls, not causal estimates.

`BlendProvider` holds named `ApiModelProvider` candidates and routes by alias.
`BlendRoutingPolicy` supports a default model, next-call model after successful
or failed tools, and recovery after a configured no-progress threshold. The
next-call tool outcome is aggregated across the completed tool batch: any
failed tool selects the error route, even when another tool in that batch
succeeded. The
server can configure candidates through `ARABICA__BLEND_MODELS`; the CLI reads
named aliases and model IDs from `~/.arabica/config.toml`. All candidates
currently share one API type, base URL, and key. Terminal chat, one-shot runs,
and ACP sessions use the same policy and provider pool; ACP exposes aliases in
its model selector and treats the selected alias as that session's default.

The evaluator reports run completion states, not verified task correctness.
It does not yet record an observation for a provider call interrupted by
cancellation. Policy identity is currently the configured ID and version; it
does not hash the full policy and model registry snapshot. ACP model selection
changes the session's default alias while retaining the other routes.

This slice does not automatically retrain or promote policy versions. It
provides auditable outcomes and deterministic rules that can be revised by
changing the policy version. A later increment needs a durable evaluation
consumer, comparable task outcomes, and guarded canary promotion before
automated periodic tuning can be enabled.

## Goal and boundary

Blend selects one configured model for **each model call** within an Arabica
run. It updates its selection policy from measured outcomes, while preserving
the existing event order, provider-neutral model request, tool permissions,
and session lifecycle. The first version uses a deterministic rule policy;
later versions may use an offline-learned or contextual-bandit policy when
evaluation data supports it.

There are two distinct kinds of dynamism:

1. **Within-run adaptation:** the next model can change after a completed
   model/tool step as the run state changes.
2. **Across-run adaptation:** an evaluated policy version can replace the
   current policy for future decisions. Each decision retains the exact policy
   version and feature snapshot needed for audit.

Blend does not route each streaming token or each event independently. A
routing boundary occurs immediately before `ModelProvider::complete`, after
the Runtime has projected memory and collected the previous step's tool
results. A run may use several models; one model call has exactly one selected
model and one immutable decision ID.

## Current code and extension points

- `crates/arabica-runtime/src/lib.rs`: `CoreRuntime<M, R>` owns one `model: M`,
  constructs `ModelRunRequest` at each step, and calls `self.model.complete`.
  This is the natural decision boundary. Its loop already tracks model steps,
  tool errors, no-progress behavior, completion, and cancellation.
- `crates/arabica-provider/src/lib.rs`: `ModelProvider` defines `complete` and
  `cancel`. `ApiProviderConfig` has one API type, URL, key, and model. Its
  `RuntimeRequest` records the concrete model name; `RuntimeUsage` records
  token and cache usage but has no prices or elapsed time.
- `crates/arabica-protocol/src/lib.rs`: typed `Event` variants record prepared
  model requests, ordered response items, response completion, tool outcomes,
  and run terminal states. Model exchange events are internal, not normal
  client events. The protocol version is currently `1.0`.
- `crates/arabica-session/src/lib.rs`: the session log allocates deterministic
  sequence numbers and persists internal and visible events. The Runtime must
  append decisions here, never create a second mutable run history.
- `crates/arabica-cli` and `crates/arabica-server` currently construct a single
  provider configuration. The benchmark package has token/latency accounting
  for controlled experiments, but it does not yet evaluate Blend policies.

The first implementation should add routing behind the existing
`ModelProvider` port and keep `CoreRuntime` generic. A `BlendProvider` can own
the configured candidate providers and implement that port. Runtime owns the
policy and decision event; the provider pool only executes a selected alias.
This separates task semantics from API transport.

## End-to-end lifecycle

1. **Build the request.** Runtime projects canonical events into short and
   run memory, applies disclosure/tool policies, and constructs the same
   provider-neutral request used today.
2. **Freeze observable features.** Derive a `RoutingContext` from events
   already sequenced before this model call. Include the run/step IDs, trigger
   class, task phase, last tool outcome class, consecutive no-progress count,
   prior model aliases, remaining budget, required capabilities, and projected
   request size. Never include future tool outcomes or run completion.
3. **Filter candidates.** Enforce hard constraints before scoring: model
   availability, supported tools/structured completion/reasoning mode,
   context capacity, data residency, disclosure policy, price and time ceilings.
   A model is ineligible when required capabilities are unknown.
4. **Choose once.** Evaluate the active policy against the eligible set. It
   returns alias, reason code, policy ID/version, decision ID, and optional
   candidate scores. Ties use a stable declared alias order. If no candidate
   qualifies, use an explicitly configured safe fallback or fail before the
   provider call; never silently downgrade a hard requirement.
5. **Record the decision.** Append `model.route.selected` before the prepared
   model request. The event contains metadata and bounded feature values, not
   credentials, full prompts, or private endpoints.
6. **Execute.** `BlendProvider` resolves the alias to the already validated
   adapter. The prepared request's concrete model must match the decision.
   Stream progress remains associated with that decision; cancellation reaches
   the selected provider.
7. **Observe.** Append normal response and tool events. An asynchronous
   evaluator joins the decision to these events and creates a separate
   evaluation record once the observation window closes.
8. **Adjust.** A scheduled offline job considers mature evaluations,
   proposes a new immutable policy version, tests it, and promotes or rejects
   it. Active runs pin a policy version at run start for reproducibility;
   within-run model selection remains dynamic under that version.

## Typed contracts

The following are intended API shapes, not committed Rust signatures:

```text
ModelAlias = validated stable name, distinct from vendor model ID
RoutingContext = { session_id, run_id, model_step, trigger, phase,
  last_tool_outcome, no_progress_steps, retry_count, previous_alias,
  request_size_bucket, capability_requirements, budget_remaining,
  disclosure_level, feature_schema_version }
RoutingDecision = { decision_id, policy_id, policy_version, selected_alias,
  eligible_aliases, reason_code, feature_digest, selection_probability? }
RoutingEvaluation = { decision_id, evaluator_version, observation_window,
  immediate_signals, run_outcome?, cost_estimate?, latency_ms?,
  attribution_method, evidence_event_ids, status }
```

`RoutingContext` should use enums and bounded numeric fields, not an opaque
JSON bag or raw prompt text. `feature_digest` is computed from a canonical
serialization of the redacted feature snapshot. Store that snapshot privately
when replay and training require it; a digest alone cannot reconstruct a
decision. A decision ID can be derived from `(run_id, model_step)` if uniqueness
is guaranteed across retries and restores, otherwise use a persisted unique ID.

Add a typed internal protocol event such as
`ModelRouteSelected { model_step, decision_id, policy_id, policy_version,
 selected_alias, reason, feature_digest }`. The event must precede
`ModelRequestPrepared` and every response event for that step. Update explicit
matches in short-memory projection and ACP mapping; route metadata is audit
evidence, not conversational memory or a user-facing SSE payload. The prepared
request continues to hold the concrete model used. New event variants require
schema/serialization and snapshot compatibility tests; review whether the
`1.0` protocol contract permits an additive internal event or requires a
version change before release.

Store evaluation and policy-promotion records separately from the canonical
session history if they arrive after run completion. They must reference
immutable event IDs and versions. If evaluation records are exposed through
the protocol, use typed events with an explicit owner and sequencing contract;
do not append them retroactively into a closed run.

## Policy v1: an auditable recipe

The initial recipe is configuration, not a model deciding which model should
run. Example rules, in descending priority:

```text
if typed completion/tool schema required: only capability-certified models
if repeated tool error or no progress: recovery-capable tier
if first step or new plan needed: planning tier
if bounded extraction after successful retrieval: inexpensive tier
if final synthesis with substantial evidence: synthesis tier
otherwise: default tier
```

Rules select from aliases such as `fast`, `balanced`, and `deep`, not hard-coded
vendor names. A `fast` model is never assumed capable of tool calling merely
because its cost is lower. Constrain consecutive tier changes to prevent
oscillation, with a defined exception for failures and hard capability needs.
The policy records why a route happened and which constraints excluded other
models. A configuration change creates a new policy version and is validated
before activation.

The model registry should contain alias, API type, model ID, credential
reference, endpoint reference, capability declarations, context limit,
per-token prices and currency, timeout, and availability state. Keep actual
secrets in the current host credential mechanisms; logs and policy files
contain references only. Define deterministic precedence between CLI flags,
workspace/user config, environment, and Blend policy. An explicit user model
override should pin that model for the run and be visible in the decision
reason. Legacy single-model settings remain a one-candidate Blend with
identical behavior when Blend is disabled.

## Evaluation: every decision, with delayed credit

Every selected route gets an evaluation record, including failures, timeouts,
cancellations, and missing usage. This is primarily **measurement of the chosen
action**, not proof that a different model would have done better. Never infer
counterfactual quality from an unexecuted alternative.

Three observation windows serve different purposes:

- **Immediate (after model response):** provider/transport success, validity
  of decoded output, finish reason, token/cache usage, call latency, schema
  conformance, and whether the response proposed admissible tool calls.
- **Local (after subsequent tool results):** tool error rate, invalid
  arguments, permission denial, repeated calls, loop guard action, and
  measurable progress on the requested subgoal. A denied tool is not
  automatically a model-quality failure; distinguish policy restriction from
  bad model choice.
- **Terminal (run completion or defined timeout):** task success from a
  deterministic oracle where available, completion status, total cost,
  end-to-end latency, retries, and safety incidents. A user-facing final
  response is not itself proof of correctness.

Keep `unknown` distinct from `false` and from zero. Provider usage fields may
be unreported; pricing can be stale; some tasks have no objective oracle.
Record price-card version and currency alongside cost estimates. Use measured
latency from a monotonic clock. Count actual billed calls; a replayed response
in the benchmark harness must not be counted as a new charge. Preserve
cache-read and cache-write token accounting because switching models can
change effective cost even when raw token totals fall.

For policy learning, attach terminal outcomes to all contributing decisions
with an explicit attribution method. Start with conservative trajectory
attribution: every step contributes to run-level success/cost, but do not label
every step independently "correct" on success or "wrong" on failure. Where a
local oracle exists (e.g. expected tool result or exact file output), record
that stronger per-step evidence. Use human review or a calibrated judge only
for sampled cases without an oracle, and store evaluator version and
disagreement rates. Separate quality, cost, latency, and safety metrics;
combine them into a policy objective only after declaring minimum quality and
safety gates.

For sparse or delayed feedback, use `pending`, `mature`, `censored`, and
`invalid` evaluation states. Cancelled runs and external outages are censored
for quality comparisons but remain in availability and cost accounting.
Provider errors should not become model reasoning failures. No automatic
training data may include secrets or unredacted private memory.

## Regular policy adjustment

The cadence is configurable; a safe starting schedule is daily aggregation,
weekly candidate policy generation, and monthly review of model capabilities,
prices, and benchmarks. The schedule does **not** automatically promote a
candidate just because time elapsed. Require enough mature, comparable data
per route and task stratum. Continue the current policy when evidence is weak.

Each adjustment cycle:

1. Freeze a cutoff time, source event range, feature schema, evaluator version,
   candidate registry, and price card. Exclude pending evaluations.
2. Check data quality: join completeness, missing usage, sample size,
   eligibility drift, model version drift, duplicate decisions, and workload
   mix. Segment by task phase, capability need, context size, and provider.
3. Build a candidate. Initially tune rule thresholds or alias ordering within
   declared safe bounds. A later learned router may use contextual bandits or
   supervised models, but must log selection probabilities if off-policy
   estimators are planned.
4. Compare with the incumbent on held-out tasks and paired, isolated replay
   where feasible. Running a different model changes later tool actions, so
   offline replay of logged responses alone cannot establish end-to-end
   superiority. Never replay effectful tools against production systems.
5. Run a canary on whole runs or sessions, not individual steps, so the
   comparison does not contaminate trajectories. Stratify assignment and
   report uncertainty, success, cost per successful task, p95 latency,
   provider errors, and safety/permission outcomes.
6. Promote only if predeclared quality/safety floors pass and the stated
   cost/latency objective improves by a meaningful margin. Record approval,
   policy hash, effective time, and evidence window. Retain the previous
   policy for immediate rollback and do not rewrite historical decisions.

A drift alarm should trigger an early review when model versions, price cards,
error rates, task mix, or evaluator calibration change materially. An
availability failure may cause a preconfigured emergency failover; record it
as a separate reason, not as evidence that the replacement model is better.
Exploration, if introduced, must be bounded to eligible low-risk strata and
have a hard budget; start at zero exploration in production.

## Cross-provider continuity and failure handling

- Recompile each call from canonical events and provider-neutral items. Do
  not transfer provider-private continuation IDs, opaque reasoning bodies, or
  cached prompt handles across models. Validate tool-call/result pairing and
  provider-specific unsupported items before selecting a target.
- A provider failure before a response may trigger at most a configured,
  bounded fallback. Record every attempt, its model and cost/latency, and the
  final outcome under one decision lineage. No fallback after an effectful
  tool action without idempotency and explicit retry semantics.
- On cancellation, stop the active selected provider and preserve the
  existing run-cancelled event order. A timed-out call is not silently
  retried with a different model if the provider may have completed it.
- Tool permissions, disclosure limits, execution roots, and terminal
  controller requirements remain hard constraints across all candidate
  models. Routing never grants permissions or broadens context.
- Policy or model-registry loading must be atomic: validate a complete new
  snapshot before swapping it in, pin in-flight runs, and retain a rollback
  snapshot. A bad policy is a configuration error, not an excuse to guess.

## Implementation increments and acceptance gates

### A. Measurement baseline

Add decision IDs and per-call monotonic latency in the existing single-model
path, plus an offline evaluator that joins model, tool, and run events. The
single candidate is always chosen. Gate: complete and correctly ordered
records for success, provider failure, cancellation, tool denial, and restore;
cost is labeled unknown when prices or usage are unavailable. This phase
establishes an unbiased baseline before optimizing routes.

### B. Static multi-model Blend

Add validated model registry and `BlendProvider`, then typed route policy and
event. Wire both CLI and server through their composition layer. Gate: model
switches after a tool step, typed completion remains supported, no secret is
serialized, and single-model behavior is unchanged. Validate unsupported
capabilities and cross-provider continuation with conformance tests.

### C. Dynamic within-run recipe

Implement the priority rules and bounded escalation. Gate: deterministic
replay of decisions from pinned policy plus canonical event prefix;
session sequences remain contiguous; no route change from streaming fragments;
no oscillation under repeated benign events; cancellation reaches the active
provider. Include fixtures for tool failure, no progress, budget exhaustion,
unavailable model, and multiple completed tool calls in one step.

### D. Evaluation and controlled campaign

Extend `benchmarks/runtime-short-memory` rather than creating a parallel
experiment package. Add a Blend track with frozen tasks, candidate models,
pricing, capability matrix, run assignment, and objective success oracle.
Compare fixed strong, fixed inexpensive, fixed phase recipe, and adaptive
event/state recipe. Include trials where cross-model context continuity and
cache loss matter. Gate: reproducible provenance, separate billed and replayed
calls, per-stratum results with uncertainty, and no improvement claim based
only on tokens or a model-as-judge score.

### E. Versioned adjustment loop

Add offline aggregation, candidate generation, canary assignment,
promotion/rollback, and drift checks. Gate: policy changes affect only new
runs, historical decisions reproduce exactly, low-sample cohorts cannot
promote automatically, and rollback restores the prior version without
changing session histories. Defer any learned router until a rule baseline
and sufficiently reliable labels exist.

Run `cargo fmt --all --check`, relevant workspace tests, and
`cargo clippy --workspace --all-targets -- -D warnings` for implementation
changes. Protocol changes also need serialization/schema and restore tests.

## Open design decisions for the first implementation PR

- Whether the execution alias belongs in `ModelRunRequest` or is passed through
  a new routing-aware provider port. Favor a typed field or typed call
  parameter; never smuggle it through text instructions.
- Whether policy versions are persisted as content-addressed local files or a
  separate adapter store. Either must survive restart and be recoverable for
  event replay.
- Exact model capability schema, particularly tool calling, typed terminal
  completion, images, reasoning, and context window behavior.
- Initial price-card source and update policy; missing price means unknown
  cost, not free usage.
- Which task cohorts have a trustworthy success oracle. Do not optimize
  quality from run completion alone.

## Immediate implementation plan

1. **Route from complete step outcomes.** Aggregate all tool results in a
   completed model step, distinguish error classes, and define Blend progress
   signals without changing the runtime's existing hard-stop semantics.
2. **Pin reproducible policy state.** Record a policy content hash, model
   registry snapshot, and explicit per-run model override semantics. ACP
   changes to a session default must receive a distinct policy identity.
3. **Complete call observations.** Record cancellation and missing usage as
   explicit observation states, and keep task success separate from run
   completion.
4. **Filter and stabilize routes.** Validate required model capabilities,
   context limits, and hard constraints before selection; then add bounded
   switching behavior with explicit recovery exceptions.
5. **Run controlled Blend experiments.** Compare fixed strong, fixed
   inexpensive, fixed stage-based, and state-aware routing on frozen tasks.
   Report verified success, cost per successful task, latency, recovery, and
   uncertainty before proposing policy tuning.
6. **Test memory-aware routing.** First vary memory evidence while holding
   routing fixed; only combine memory and routing policies after an isolated
   experiment shows a measurable benefit.

Each behavior change needs focused tests. Protocol changes also need
serialization/schema and restore coverage. Automatic policy learning and
promotion remain gated on mature evaluations and controlled canary evidence.
