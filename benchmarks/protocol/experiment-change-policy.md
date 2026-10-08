# Experiment change and reproducibility policy

Effective date: 2026-09-30. Scope: the maintained Rust experiment package in
`benchmarks/runtime-short-memory`, its production dependencies, and retained
JavaScript fixtures, verifiers, and proxies. This policy covers internal
ablations and comparisons with external agents.

This is a prospective operating policy. It does not retroactively validate
historical results or revive withdrawn protocols. Each campaign needs a frozen
protocol with concrete settings; this document defines its change boundaries.
Existing historical reports and raw artifacts remain preserved.

## 1. Experimental stages

1. **Development and preflight:** fix implementations, qualify provider parameter
   support, tune on development tasks, and verify task correctness and mechanism
   activation. Record exploratory results; exclude them from confirmatory claims.
2. **Frozen execution:** pin the implementation, tasks, environment, generation
   settings, measurement method, and analysis rules before collecting scored
   results. Run the declared schedule and preserve every attempted run.
3. **Analysis:** apply the frozen analysis to immutable artifacts. Record changes
   to analysis software and regenerate all affected reports consistently.
4. **Successor experiment:** behavioral optimization creates a new implementation
   and campaign version. Freeze it and rerun affected comparisons.

Inspecting formal outcomes makes those tasks development evidence for subsequent
outcome-driven tuning. A successor confirmatory claim needs an untouched test
set or a prospectively specified new evaluation design. Merely assigning a new
campaign ID does not undo exposure to the previous test results.

## 2. What may be changed

### Allowed without rerunning model trajectories

- Documentation, comments, and presentation fixes that do not alter execution,
  serialization, measurement, or interpretation.
- Offline plots and summaries generated from immutable raw evidence.
- A demonstrable analysis bug fix, applied to every affected arm and sample.
  Preserve the old report, issue a correction, record both analysis versions,
  and explain whether the conclusion changed.
- Additional offline integrity checks and derived diagnostics that do not
  replace the frozen primary metric. Label post-hoc findings exploratory.

Adding fields to future logs, renaming executable identifiers, or changing a
dependency is not automatically non-behavioral. If request bytes, timing,
ordering, resource use, or failure behavior can change, follow the versioned
change process below. Timing experiments also freeze logging and profiling.

### Allowed only as a versioned change with affected experiments rerun

- Runtime memory policies: TTL, decay, Batch admission, protected tails, PGC and
  FBGC thresholds, checkpoint frequency, continuation estimates, and GC costs.
- Projection and recovery: message ordering, boundaries, pointer formats,
  hydration, truncation, compression, archive I/O, and serialization.
- Provider behavior: model, endpoint, API type, seed, temperature, top_p,
  thinking, output cap, tool schema, tool choice, and prompt construction.
- Runner behavior: tool implementations, output limits, filesystem state,
  sandbox permissions, timeout, concurrency, retries, and cancellation.
- Workloads and scoring: generators, task sizes, datasets, fixtures, verifiers,
  success criteria, eligibility gates, exclusions, and statistical rules.
- Performance changes, dependency updates, tokenizer/template changes, and any
  instrumentation that can affect the measured execution.

Symmetric deployment to all arms is necessary for shared controls but does not
permit mixing old and new trajectories. A treatment-specific algorithm change
is allowed in a successor experiment when declared as the treatment; unrelated
assistance to that arm is a confounder.

## 3. Required freeze record

Before formal execution, archive a protocol and manifest containing:

- Campaign ID, stage, hypothesis, primary contrast, primary metric, and scope of
  the claim: mechanism isolation or independent end-to-end performance.
- Commit, dirty-tree diff, relevant untracked sources, lockfile, executable and
  source hashes, toolchain, dependency/build settings, and analysis version.
- Tasks and dataset revisions/hashes; generators and seeds; exact prompts,
  tool schemas, fixture/verifier versions; workspace initialization and resets.
- Each arm's configuration, including all intended treatment differences and
  all shared controls. External agents include version, adapter, permissions,
  tools, context budget, and supported generation controls.
- Provider/model identifier, private endpoint identity, API type, supported
  sampling parameters, explicit thinking behavior, output/context limits,
  timeouts, retry policy, concurrency, and provider-version metadata if exposed.
- Repetitions, paired sample IDs, execution schedule/order, stopping rule,
  missing-pair policy, infrastructure-error classification, and replacement
  rules. Sample size must be justified for the campaign; five pairs are not a
  universal statistical sufficiency guarantee.
- Correctness and mechanism gates, quality margin if used, cost denominator,
  aggregation, uncertainty method, and all predeclared exclusions.
- First-response reuse mode, logical-versus-actual call accounting, and the
  independent cache simulation policy described below.

Credentials and authorization headers must not enter committed manifests.
Raw prompts, responses, tool outputs, and private endpoint details remain in
private local artifacts. Record the start/end time and every attempt. A seed is
a requested control, not proof of deterministic provider generation.

## 4. Explicitly prohibited special adjustments

The following actions are prohibited within a frozen campaign. Where a change
is scientifically useful, it must become a declared successor experiment.

### A. Arm-specific assistance and answer leakage

- Giving only one arm a better model, extra thinking, a larger output/context
  budget, more steps, extra retries, longer timeouts, or privileged tools.
- Adding treatment-specific reminders, recovery prompts, examples, or hidden
  system instructions outside the declared memory-policy difference.
- Injecting gold answers, expected file contents, verifier feedback, future tool
  results, or retrieval hints unavailable under the frozen task contract.
- Letting a runner or adapter infer the expected answer from a task ID, path,
  benchmark marker, fixture flag, or evaluation-only metadata and return it to
  the model. Explicit task content legitimately visible to every arm is allowed.
- Replacing model-generated content with the oracle, repairing output files
  after the run, or passing a fixture-generated result off as a live model run.
- Using special tools or wrappers that silently perform multiple required
  actions, expand placeholders, or copy expected files for one arm. Such
  shortcuts change the task and cannot establish a memory-policy improvement.

### B. Output and context manipulation

- Suppressing reasoning or shortening output only for the treatment to make its
  token totals look better, unless that setting is the explicitly studied factor.
- Expanding repetitive payloads inside a tool while counting only a short model
  command, when the frozen workload requires the model to produce exact content.
- Removing irrelevant context only from B0 or silently turning Full Replay into
  Tail-K, capped replay, summarization, or retrieval.
- Changing TTL/GC thresholds after inspecting a task to ensure a favorable GC
  point; special-casing individual benchmark inputs in production policy.
- Giving a pointer private access to an answer or bypassing archive integrity,
  relation closure, protected anchors, or recovery checks to report compression.
- Forcing later model responses or tool actions to match another arm while
  presenting the run as an independently generated end-to-end trajectory.

Different later outputs are legitimate consequences of different contexts.
Do not artificially equalize them. Shared-first experiments only reuse the
declared first response and report that intervention explicitly.

### C. Sampling, retries, and selective reporting

- Repeatedly requesting a first response until a preferred length, tool call, or
  outcome appears; choosing the best seed after inspecting formal results.
- Retrying only the worse arm, silently falling back to a different model or
  endpoint, dropping unsupported parameters, or changing thinking mid-run.
- Continuing an interrupted trajectory from a hidden edited state and labelling
  it as an uninterrupted fresh run.
- Deleting failed, timed-out, cancelled, malformed, or expensive attempts;
  choosing the best repetition; replacing an unsuccessful run without disclosure.
- Stopping when significance or a desired saving appears, adding repetitions to
  rescue a claim, or changing execution order based on observed performance.
- Calling model/task failures infrastructure failures to exclude them. Apply the
  predeclared classification rule using retained evidence.
- Dropping one member of a pair and reporting the remaining run as paired
  evidence. Report valid-pair counts, missingness, and all arm-level failures.

Predeclared infrastructure replacements must preserve the original attempt,
reason, and linkage. Use fresh paired workspaces and the frozen pairing policy;
never overwrite the failed evidence. Task failures are outcomes, not convenient
replacement candidates. If no replacement rule was frozen, mark the campaign
incomplete and issue a successor plan rather than inventing one after the fact.

### D. Cache and accounting adjustments

- Using vendor `cached_input_tokens` as the independent cache result, or fitting
  the simulator to vendor cache telemetry after seeing an arm's results.
- Giving an arm a warm simulated cache, sharing cache state across arms, or
  selectively excluding cache-reset calls and hydration costs.
- Counting suffix matches, approximate matches, raw HTTP JSON tokenization, or
  character-to-token estimates as exact model-input prefix hits.
- Changing tokenizer, chat template, special tokens, tool serialization, block
  rounding, eviction, output-cache policy, or cache lifetime within a comparison.
- Counting a replayed first response as another paid API call, or labelling the
  legacy logical usage total as measured actual API usage.
- Claiming actual billing savings from a prefix simulation without a separately
  specified pricing/accounting model and the costs it includes.
- Silently replacing the runtime's vendor-informed GC admission cost with the
  independent simulator during formal execution. Admission is algorithm behavior;
  independent cache analysis is measurement. Changing either requires versioning.

### E. Scoring, provenance, and interpretation

- Weakening exact file-content checks, changing the oracle after observing
  failures, or reporting aggregate subcheck scores as full task success.
- Changing the primary metric, quality margin, exclusions, statistical test, or
  averaging rule after seeing results without marking the analysis exploratory.
- Combining trajectories from different code, model, task, or configuration
  versions into one frozen comparison; using historical results as a fresh arm.
- Claiming a GC mechanism benefit when no GC happened, or treating a failed
  correctness gate as acceptable solely because fewer tokens were submitted.
- Editing raw requests/responses, reserializing and replacing original wire
  evidence, overwriting result directories, or rebuilding and substituting the
  executable associated with completed runs.
- Reporting shared-response runs as independent replicates, treating per-call
  observations as independent task repetitions, or hiding known test-set tuning.

## 5. First-turn pairing and independent cache rules

For shared-first mechanism experiments, corresponding repetitions must have
identical serialized first requests, including generation controls. Archive the
complete first request and response and their hashes. Reuse that response once
per pair, label its origin, and reject a request mismatch before a model call.
Do not select a favorable source response. Declare which arm supplies it and
the execution order; changing the source/order starts a new campaign version.

Maintain separate logical-trajectory and actual-API accounting. Replayed usage
can be retained for identical runtime behavior, but must be excluded from actual
API totals. Report the shared source call explicitly and declare its allocation
when presenting comparable costs. Existing report totals alone do not establish
actual billed-call totals. Independent end-to-end experiments generate both
first responses separately under the same supported controls.

Independent prefix analysis uses complete model-input token IDs produced by a
fixed tokenizer and chat template, including system text, tools, and special
tokens. A hit is a contiguous prefix already present in that arm's declared
cache state; the first mismatch ends it. Each arm starts cold. Freeze whether
cache state resets per task, persists across repetitions, includes generated
outputs, uses eviction, or rounds to blocks. Shared-first replay must have an
explicit simulated cache-state transition; it is not a real API prefill.

The current `prefix_cache` tool retains prior input sequences, excludes generated
outputs until they appear in a later input, uses token-granular hits, and has no
eviction. It starts cold per supplied arm; task-level reset requires separate
input groups/runs. Label this an ideal input-prefix simulation. Without the
model's tokenizer and template, report bytes/estimates with their limitations;
do not issue precise independent KV-token claims. Provider telemetry may be
retained as separately labelled provenance, including its use by runtime policy.

## 6. Bug fixes and deviations

1. Stop affected execution; retain completed and partial artifacts.
2. Record the issue, affected paths, first discovery time, affected runs, and
   whether exposure to outcomes motivated the change.
3. Determine whether the issue affects execution, measurement only, or the
   frozen scientific design. Do not assume an equal fix cancels across arms.
4. For execution changes, fix and test the code, assign a new version, freeze a
   successor manifest, and rerun all affected arms and samples. Conservative
   default: rerun the whole comparison block. Reuse unaffected evidence only
   with a recorded independence/compatibility justification.
5. For measurement-only fixes reconstructible from sufficient raw evidence,
   rerun the corrected analysis for every affected sample and preserve old
   reports. Missing raw evidence requires new execution or a missing-data label.
6. Record deviations and their impact. Exploratory findings cannot silently
   become the original confirmatory result.

An unexpected provider-version change, unsupported control, first-request
mismatch, corrupt archive, or altered verifier is a deviation requiring this
process. A timeout or a task failure under unchanged frozen conditions remains
a recorded outcome subject to the predeclared handling rule.

## 7. Acceptance and reporting

Before formal collection, preflight must establish parameter support, first-turn
pairing, archive integrity, exact task correctness, and the intended GC behavior.
Choose the workload and activation criteria in preflight and freeze them.
Do not force activation by changing thresholds during formal execution. Report
all activation/non-activation outcomes under the declared analysis population.

Formal reports identify the campaign/version, task and pair counts, attempts,
quality failures, infrastructure failures, deviations, mechanism activation,
input/output cost measures, independent cache assumptions, and uncertainty.
Efficiency claims require the frozen quality gate as well as the cost criterion.
Changes to these requirements belong in a successor protocol.

For every proposed adjustment, record: what changes, why, whether behavior or
measurement changes, which arms/samples are affected, which new version applies,
and whether model reruns or full reanalysis are required. If equivalence cannot
be established, treat the adjustment as behavior-changing.
