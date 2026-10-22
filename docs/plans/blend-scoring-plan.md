# Automatic task scoring and Blend policy iteration

Status: proposed implementation plan, 2026-10-21. This document specifies future
work; it does not claim that weighted scoring or policy promotion is implemented.

## Objective and existing foundation

Produce an automatic, reproducible score for each evaluation task, then compare
Blend policies on identical tasks. Retain the evidence and score components so
that improvements and regressions can be explained.

Reuse the Rust runtime, `RunAcceptanceScorer`, detached evaluation worker,
SQLite adapters, and the existing `policy_campaign` binary in
`benchmarks/runtime-short-memory`. See [run evaluation](../run-evaluation.md)
for the current execution and acceptance interfaces and the
[Blend design](../arabica-blend-design.md) for routing context.

## PR 1: Versioned scoring contract

Define typed contracts for:

- Task specification: identity/version, initial environment fingerprint,
  acceptance criteria, and resource budgets.
- Acceptance criterion: checker, parameters, weight, and required-pass flag.
- Scoring profile: formula version, weights, reference cost, reference time.
- Task score: status, quality, efficiency components, total, and evidence IDs.
- Evaluation identity: checker version, task fingerprint, scoring fingerprint.

Extend the existing `RunAcceptance` result in
`crates/arabica-runtime/src/run_evaluation.rs` rather than adding a parallel
type. `verified_success` keeps its current meaning (the run completed and every
criterion passed); the required-criteria gate is reported as a new, separate
field. `completion_bps` remains the unweighted share of known criteria passed,
and weighted quality `Q` is a new, separately named field. `coverage_bps` remains
the share of criteria with known results. Existing consumers, including
`arabica evaluations compare` and the desktop, keep their current meanings;
any display of `Q` labels it as weighted quality, not completion.

Validate weights when a task specification is loaded: every weight must be
finite and non-negative, and at least one criterion must have a positive
weight. Reject specifications that violate this rather than scoring them.
Required criteria may carry zero weight; they gate the score without
contributing partial credit. Normalize the validated weights to sum to one.
Each known criterion result is in `[0, 1]`; binary checks return zero or one.
Define quality as:

```text
Q = sum(criterion_weight * criterion_score)
E_cost = 1 / (1 + actual_cost / reference_cost)
E_time = 1 / (1 + active_time / reference_time)

If any required criterion fails, or the task exceeds its declared budget:
    Score = 0
Otherwise, when required criteria pass and all scoring evidence is available:
    Score = 100 * Q * (0.70 + 0.20 * E_cost + 0.10 * E_time)
```

The weights are initial proposed defaults, not empirically validated values.
Reference values must be positive and frozen per task and scoring version.
Obtain them from separate fixed-baseline calibration runs, such as medians
across strong-model runs. Do not derive them from the candidate being scored.

Unknown evidence never passes a check or causes weight renormalization. A known
required failure is sufficient for a zero score even if other evidence is
missing. Distinguish pending checks, incomplete evidence, invalid evaluator
execution, and completed scoring. Evaluator infrastructure failure is not a
task failure. Failure classification must be fixed before a campaign,
including treatment of provider outages.

Missing data is resolved by these deterministic rules, applied in order:

1. Any known required failure gives `Score = 0` (status: scored failure).
2. Budgets are checked against known lower bounds. Known cost and known active
   time are sums over calls and spans with established values. If either known
   lower bound already exceeds its budget, `Score = 0` (status: scored
   failure); unknown parts cannot rescue an over-budget trial.
3. If any required criterion is unknown, the trial is incomplete and has no
   total score.
4. If a budget cannot be decided because the known lower bound is within budget
   but some usage is unknown, the trial is incomplete for that budget.
5. Otherwise compute `Q` from known criteria; unknown non-required criteria
   contribute zero, never renormalized weight. If cost or time is not fully
   known, the trial receives a quality-only score with `E_cost` or `E_time`
   set to zero, and its status records which efficiency terms were missing.
   This is a conservative lower bound, not an unscored trial, so partial usage
   from a retried call cannot make a whole trial unscorable.

Retain every available quality and accounting component in all cases.

Acceptance: deterministic calculation, valid bounds, rejection of invalid
weights, each missing-data rule above, and monotonic cost/time penalties.
Cover success, failure, budget exhaustion, budget undecidable from partial
usage, unknown checks, missing prices, and zero/negative/non-finite weights
with focused unit tests.

## PR 2: Trustworthy automatic acceptance

Extend the existing scorer with weights and required criteria. Initially add:

- Exact file-content checks.
- JSON schema and field assertions.
- Controlled test execution for fixes and regressions.
- Checks that changes stay within permitted files.

Keep tests and acceptance definitions under evaluator control, outside the
agent's mutable task files. Execute tests in an isolated, bounded environment;
test commands and submitted artifacts are untrusted execution inputs. Give
checkers separate timeouts and output limits. Distinguish failed assertions
from a checker that could not execute.

Freeze selected artifacts immediately after the trial terminates. Store bounded
evidence and test-result summaries for audit. Rescoring must use frozen evidence,
not a subsequently modified workspace. Self-reported completion is insufficient.
Defer model judges to a separate task category with a fixed rubric and recorded
judge version.

Acceptance: reject false completion claims, wrong or missing artifacts, and
attempts to obtain credit by editing tests. Evaluator faults must remain distinct
from task failures. Preserve private artifact storage protections.

## PR 3: Complete cost and time accounting

Add a versioned price card with currency, model mapping, and applicable billing
rules. Account for main calls, planning/auxiliary calls, retries, recovery, cache
reads/writes, and billable failed calls where billing can be established.
Missing usage or pricing remains unknown and feeds the PR 1 missing-data rules:
known values still form a cost lower bound, and unknown parts only remove the
efficiency term. Report accounting coverage; do not count replayed
observations as newly billed calls.

Retain wall time, approval-wait time, active time, and model/tool durations.
Use active time for the proposed score and show wall time separately. Keep
benchmark permission settings consistent. Record evaluator costs separately
from the agent's task costs.

Acceptance: verify known billing fixtures, auxiliary calls, missing usage,
cache accounting, and duplicate/replayed observations.

## PR 4: Frozen task suite and end-to-end campaigns

Create 12 initial tasks, three in each category:

| Category | Acceptance evidence |
| --- | --- |
| Exact file operations | Content and permitted-change assertions |
| Structured extraction | Schema and expected field values |
| Small code fixes | Evaluator-controlled tests and regressions |
| Multistep work and recovery | Final-state assertions and tests |

Compare fixed strong, fixed inexpensive, fixed-stage Blend, and state-aware
Blend policies. Run a smoke subset first. The initial full matrix is 12 tasks
times four policies times three repetitions: 144 trials. This validates the
pipeline and identifies large differences; it is not sufficient evidence by
itself for automatic promotion.

Extend `policy_campaign` to freeze task, registry, policy, pricing, and scoring
versions; preserve identical initial conditions and isolated trial directories.
Record deterministic ordering or assignment seeds. Persist every completed
trial, support restart without duplicate billing, and enforce a campaign budget.
An interrupted in-flight trial needs explicit recovery handling rather than a
claim of exactly-once provider execution.

Acceptance: one command executes, checks, prices, scores, and summarizes a
campaign. Every score links to evidence. Interruptions preserve finished trials.

## PR 5: Policy comparison and recommendation gates

Aggregate with task-category weights fixed before execution:

```text
StrategyScore = sum(category_weight * mean_score_within_category)
```

Include scored failures as zero. The primary `mean_score_within_category`
counts every planned trial: incomplete and invalid trials contribute zero, so
a policy cannot gain by having its failures become unscorable. Also report the
mean over completed scores only, as a secondary figure next to score coverage,
and show incomplete/invalid trial counts. Evaluator infrastructure failures
are re-run or excluded for every policy on the affected task and repetition,
so matched pairs stay matched. Compare matched tasks and repetitions; account
for dependence between repetitions of the same task when estimating
uncertainty.

Build on the existing aggregation rather than adding a second one:
`policy_campaign` already writes `comparison.json`, and
`benchmarks/runtime-short-memory/src/blend_campaign.rs` already reports Wilson
intervals for success rate by arm and task stratum. Extend those outputs with
score fields and paired score differences; keep Wilson intervals for binary
success rates.

Report overall and per-category scores, paired differences and uncertainty,
required-check success rates, total spend per successful task, active/wall time
P50 and P95, score coverage, and usage coverage. Total spend per successful task
includes failed-task spend; with zero successes it is unavailable, never zero.

Predeclare recommendation gates: adequate coverage and evidence, overall and
critical-category success floors, no prohibited outcomes, and a meaningful
score improvement. Return inconclusive when evidence is insufficient.

Acceptance: synthetic comparisons must reject misleading winners caused by
quality regression, easy-task mix, or missing evaluations.

## PR 6: Candidate generation and policy lifecycle

Replace error-count-only candidate heuristics with bounded proposals informed
by evaluated evidence. Initially vary default and post-tool models, recovery
models/thresholds, minimum dwell, and explicit tool-route assignments.
Record each change and its evidence basis. Use development tasks to propose
candidates and a separate held-out suite for final validation.

Track candidate, evaluated, approved, active, rejected, and retired states.
Automate proposals, experiments, and recommendations first; provide explicit
activation and rollback. Pin policies for existing runs and activate changes
only for new runs. Preserve full immutable policy definitions and reports.

Associate final scores with routing decisions as trajectory-level feedback,
not proof that each individual decision was correct. Keep evaluation isolated
from runtime execution and authorization.

Acceptance: trace proposals to evidence, restore a previous version through
rollback, preserve historical records, and verify evaluator failures do not
block agent execution.

## Ownership and validation

- Runtime: scoring contracts, pure calculations, checker interfaces, candidate
  logic; no experiment scheduler in the runtime.
- Adapters: evaluation evidence references, pricing, scores, policy persistence.
- CLI: evaluation, comparison, activation, and rollback composition.
- Existing benchmark package: task fixtures, isolated trials, controlled checker
  execution, campaigns, and reports; reuse runner boundaries where applicable.
- Protocol: additive typed observation facts only where accounting requires them.
- Desktop: score and evidence presentation after the core workflow is stable.

Use task-local development state and checkout-local build artifacts. For each
implementation PR run formatting, affected-package tests, and Clippy; add
serialization/restore and migration coverage for protocol or persistence changes.
Run workspace integration validation at handoff. Review execution boundaries,
artifact privacy, billing completeness, and compatibility explicitly.

The first delivery milestone is PRs 1–4: reproducible automatic scores from real
controlled trials. PRs 5–6 turn those scores into guarded policy iteration.
