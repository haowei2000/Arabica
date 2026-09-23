# Structure Context and Agent Experiment Plan — 2026-09-01

> Historical document: Python services, memory benchmark runners and Harbor
> orchestration were removed at the Rust-only cutover. References to those
> components describe the original design or campaign, not current runnable
> interfaces. See the repository benchmark guide for maintained entry points.

Status: prospective plan. No result obtained before this document may be
silently pooled into a campaign governed by this plan.

## 1. Objective

The experiment program tests three separate claims:

1. **Component claim:** each major context-management component has a measurable
   effect and preserves protocol correctness.
2. **Context-policy claim:** Structure File-Backed GC (FBGC) reduces context
   cost relative to B0 Full Replay without materially reducing task quality.
3. **Agent-stack claim:** the complete native Structure agent has a quality/cost
   advantage over selected native coding-agent stacks.

Evidence for one claim does not establish either of the other claims. In
particular, an agent-stack win does not prove that FBGC caused the win, and an
FBGC/B0 win does not prove that Structure beats another agent.

## 2. Non-negotiable experimental rules

- Freeze task definitions, prompts, tools, verifiers, models, token limits,
  timeouts, sampling parameters, and treatment order before the first scored
  Provider request.
- Do not change fixtures, prompts, verifiers, competitors, or task selection in
  response to observed results.
- General Structure fixes are allowed only between versioned campaigns. A code
  or protocol change requires a new campaign identifier and new checksums.
- Keep every attempt in the ledger, including task failures, infrastructure
  failures, timeouts, and missing usage.
- Never serialize credentials, authorization headers, private user data, or
  absolute archive roots into campaign artifacts.
- Retain lossless canonical Events, Provider request/response evidence, tool
  results, usage, mechanism telemetry, and hidden-verifier output where policy
  permits.
- Use the same terminal controller for all context-policy arms in a campaign.
- Treat a Provider seed as a matched block identifier, not as proof of exact
  reproducibility. Remote sampling variance must be estimated through repeats.
- Qualification trials are excluded from formal estimates unless the protocol
  explicitly declares a prospective sequential design before they run.

## 3. Canonical quality and cost definitions

A task trial is a **strict success** only when all of the following hold:

- the agent and benchmark process terminate without an infrastructure error;
- the output is not length-truncated;
- the hidden external verifier passes;
- the agent emits a valid terminal protocol event;
- no file outside the permitted workspace scope is changed.

Quality is evaluated over all assigned trials. The primary cost population uses
only paired or blocked observations in which every arm required for that
contrast is a strict success. A sensitivity analysis must also report all
attempts with failures explicitly shown; failures may not disappear from the
quality denominator.

Report these token quantities separately:

- total input tokens;
- cached input tokens;
- fresh input tokens (`total input - cached input` when canonically reported);
- price-weighted input using the Provider's frozen cache price;
- output tokens;
- reasoning output tokens;
- fresh input plus output for native agent-stack economic comparisons.

Secondary metrics include peak input, exact model-input bytes, Provider calls,
tool calls, failed tools, repeated calls, archive writes and reads, cache resets,
latency, duration, terminal reason, and infrastructure failure type. Secondary
metrics cannot override the quality and primary token gates.

## 4. Treatment definitions

### 4.1 Deterministic and Runtime context arms

- **B0 — Full Replay:** materialize every Provider-relevant Event in full.
- **B1 — Tail-K:** retain the last K Provider-relevant entries and structurally
  close selected tool results with their calls. B1 is an offline reference, not
  an executable Runtime treatment.
- **B2 — TTL-only:** apply typed TTL and relation decay without batch
  disclosure or recoverable pointer compaction.
- **B3 — Batch-only:** apply batch disclosure while disabling TTL and relation
  decay at the Event visibility layer.
- **S — Structure short memory:** combine production TTL, relation decay, and
  batch disclosure without Pointer GC.
- **PGC — Recoverable Pointer GC:** use TTL plus exact recoverable pointers
  without BatchKey and without durable file-backed archival semantics.
- **FBGC — File-Backed GC:** archive fully eligible closed batches durably and
  substitute exact active continuation evidence with compact recoverable
  pointers.

The primary causal ladder is:

```text
B0 -> B2 -> PGC -> FBGC
```

The alternative batch-disclosure ladder is:

```text
B0 -> B3 -> S
```

These ladders answer different mechanism questions and must not be collapsed
into one undifferentiated "Structure memory" treatment.

### 4.2 Native agent-stack arms

The first external multi-arm campaign should include:

- Structure;
- official open-source Codex CLI;
- pinned Pi Agent;
- [OpenCode](https://github.com/anomalyco/opencode);
- [Aider](https://github.com/Aider-AI/aider).

The second, heavier campaign may add:

- [OpenHands](https://github.com/OpenHands/OpenHands);
- [SWE-agent](https://github.com/SWE-agent/SWE-agent).

Native agents retain their official system prompts, repository maps, tool/MCP
surfaces, loops, and context strategies. Task input, model access, allowed
workspace changes, timeout, budget, and verifier remain common. Any wire
adapter must be lossless and shared by every affected arm.

## 5. Phase 0 — terminal-controller readiness

The v15-v18 GLM campaigns showed that a natural-language advisory plus an
optional `runtime_complete` tool is not a sufficiently reliable terminal
protocol. Large paid context experiments must wait for a stronger typed
terminal controller that remains identical across B0 and FBGC.

Required readiness checks:

- the controller never fabricates external success;
- the controller does not inspect hidden verifier results;
- invalid or mixed terminal tool calls remain canonical error Events;
- clean validation recovery receives a bounded completion opportunity;
- unfinished work still reaches a deterministic hard stop;
- lossless reasoning, tool state, Provider state, and usage survive Event
  replay and reverse Provider projection;
- B0 and FBGC use byte-identical terminal instructions and control policy.

Run a prospective 2 x 2 qualification:

```text
Memory:   B0, FBGC
Terminal: existing advisory controller, candidate typed controller
Tasks:    two predeclared qualification tasks
Blocks:   two matched blocks per task
Total:    2 * 2 * 2 * 2 = 16 paid trials
```

This phase qualifies implementation behavior only. Its results are not pooled
with the formal context campaign. Proceed only if the candidate controller
preserves external quality in both memory arms and removes the observed
differential terminal failure mode.

## 6. Phase 1 — deterministic zero-cost ablation

Run B0, B1, B2, B3, S, PGC, and FBGC on the same generated typed traces at
lower-bound Event counts of 100, 1,000, 10,000, and 100,000.

For each count and arm:

- five warm-up projections;
- twenty measured projections;
- checkpoint sizes 2, 4, and 8 where applicable;
- fixed trace fingerprints and independent evidence oracles.

Required workloads cover reasoning items, response messages, tool calls and
results, errors, repeated calls, fork lineage, working-set protection, archive
reread, and exact Provider reverse projection.

Required gates:

- deterministic replay;
- stable Event ordering and relations;
- complete required evidence;
- no orphan tool result;
- exact reasoning and Provider state preservation;
- archive checksum and idempotency checks;
- substitutive pointer transitions reduce exact continuation bytes;
- bounded projection memory and runtime.

Primary deterministic contrasts:

- B2 / B0 isolates TTL and relation decay;
- B3 / B0 isolates batch disclosure;
- S / B2 and S / B3 measure combination effects;
- PGC / B2 isolates recoverable pointer compaction;
- FBGC / PGC isolates durable file-backed archival behavior;
- FBGC / B0 measures the complete deterministic effect.

## 7. Phase 2 — live core context ablation

Run the four-arm causal ladder through the same
`SessionManager -> CoreRuntime -> Provider -> Runner` path:

```text
B0, B2, PGC, FBGC
```

Use all five pre-existing long-horizon qualification candidates. Do not remove
tasks after observing results. Within each task/block, assign the same declared
sampling seed and balance arm order with a Latin-square or Williams design.

Sample sizes:

- minimum: `5 tasks * 5 blocks * 4 arms = 100 trials`;
- target: `5 tasks * 10 blocks * 4 arms = 200 trials`.

Because v17 and v18 produced materially different FBGC trajectories under the
same declared seed, ten blocks are preferred when budget permits.

### 7.1 Primary hypothesis

FBGC is non-inferior to B0 in strict task success and improves the paired
price-weighted input ratio. Fresh input remains a co-primary reported outcome
and may not be hidden by a favorable total-input result.

### 7.2 Secondary hypotheses

- B2 versus B0 estimates the effect of typed expiry.
- PGC versus B2 estimates the effect of recoverable substitution.
- FBGC versus PGC estimates the effect of durable file-backed archival.

### 7.3 Formal decision rule

For the primary FBGC/B0 contrast:

- quality non-inferiority margin: 10 percentage points;
- at least five double-strict-success paired blocks;
- every counted FBGC observation passes its mechanism gate;
- geometric-mean FBGC/B0 primary cost ratio improves by at least 10%;
- a deterministic 10,000-resample paired-bootstrap 95% confidence interval lies
  wholly below 1.0.

The primary contrast is declared before data collection. Apply a Holm
correction to the family of secondary component contrasts. Report unadjusted
effect sizes and adjusted decisions.

## 8. Phase 3 — batch-disclosure branch

After the core campaign is frozen, compare:

```text
B0, B3, S
```

Minimum size:

```text
5 tasks * 5 blocks * 3 arms = 75 trials
```

Target size with ten blocks is 150 trials. This phase determines whether the
BatchKey route is independently useful and whether S and FBGC occupy different
quality/cost regions. It does not redefine the core FBGC/B0 primary result.

## 9. Phase 4 — FBGC component ablations

Run this phase only if Phase 2 establishes quality-matched, mechanism-active
FBGC behavior. Predeclare exact tasks and arms before any call.

Candidate one-factor treatments:

- **FBGC-no-economic-admission:** use a fixed prospective checkpoint schedule;
- **FBGC-fresh-only-admission:** set cached-input price to zero while preserving
  all reported token fields;
- **FBGC-no-working-set-protection:** disable recent successful inspection and
  error working-set pinning;
- **FBGC-no-recovery-tools:** retain pointers but remove model-visible archive
  search/read tools;
- **FBGC-memory-archive:** retain pointer semantics but use a non-durable
  in-memory archive adapter;
- **checkpoint sensitivity:** compare 2, 4, and 8 eligible batches.

Each comparison must change exactly one factor. Additive pointer projection is
retained only as a deterministic negative control because it is known not to
implement replacement semantics.

## 10. Phase 5 — cross-model confirmation

After one successful primary GLM campaign, repeat only B0 versus FBGC with at
least one additional model family that supports native Responses reasoning and
tool state.

Minimum per additional model:

```text
5 tasks * 5 blocks * 2 arms = 50 trials
```

Do not pool models. Freeze and report each Provider's cache price, request wire
format, reasoning contract, and token accounting independently.

## 11. Phase 6 — native external agent comparison

Use the frozen four-scenario repository suite and a common randomized-block
design. The initial five-agent campaign requires:

```text
5 agents * 4 scenarios * 5 blocks = 100 trials minimum
5 agents * 4 scenarios * 10 blocks = 200 trials target
```

Each block runs one trial per agent. This common-control design avoids rerunning
Structure separately for every competitor while preserving paired comparisons.

Produce four independent primary summaries:

- Structure versus Codex CLI;
- Structure versus Pi Agent;
- Structure versus OpenCode;
- Structure versus Aider.

For each comparison, require quality non-inferiority, at least five
double-success blocks, and a paired-bootstrap 95% interval wholly favoring the
declared economic metric. Do not pool competitors into one synthetic opponent.

OpenHands and SWE-agent belong in a separately frozen heavyweight campaign.
SWE-agent should be used primarily for GitHub-issue or SWE-bench-shaped tasks,
not forced into fixtures that do not match its intended interface.

## 12. Execution gates and stopping policy

Use the following order:

```text
terminal readiness
  -> deterministic ablation
  -> live core context ablation
  -> cross-model confirmation
  -> external native agent campaign
  -> optional batch and FBGC component analysis
```

Stop a qualification or safety campaign when:

- FBGC has a strict-quality failure that makes the intended comparison invalid;
- the FBGC mechanism gate does not activate;
- a credential, execution-root, task-image, or verifier integrity problem is
  detected;
- Provider accounting is missing or internally inconsistent;
- the frozen binary, source, manifest, or adapter checksum changes.

Formal fixed-sample campaigns must not stop for a favorable intermediate
estimate. They may stop for safety or infrastructure invalidity under the
predeclared rules above. A stopped campaign remains immutable and is reported
as stopped; a corrected implementation starts a new campaign.

## 13. Campaign artifacts

Every campaign directory must contain:

```text
manifest.json
preflight.json
freeze.md
checksums.sha256
ordered-trials.jsonl
summary.json
reports/
verifier/
provider-raw/       # only when scrubbed and policy permits
tool-raw/           # only when scrubbed and policy permits
```

The manifest records source revision, dirty-tree digest if applicable, task and
suite digests, model, Provider API type, sampling parameters, cache price,
timeout, limits, arm order, and exact executable versions. Reports link to
immutable artifacts but never contain secrets.

## 14. Budget envelopes

A minimum publishable program is approximately:

```text
terminal qualification:       16 trials
core internal ablation:      100 trials
one cross-model confirmation: 50 trials
five-agent external campaign:100 trials
total:                       266 paid trials
```

The preferred ten-block program is approximately 450-500 paid trials before
optional component and heavyweight-agent studies. Cost estimates must be
generated from a dry-run token envelope and frozen Provider prices before
authorization of each paid campaign.

## 15. Required final conclusions

The final report must answer, independently:

1. Does FBGC preserve strict task quality relative to B0?
2. Does FBGC reduce fresh, total, price-weighted, and peak context, and where do
   those metrics disagree?
3. Which transition in `B0 -> B2 -> PGC -> FBGC` creates the measured effect?
4. Does the effect replicate across model families?
5. Does the complete Structure stack beat each named native competitor under
   its separate paired decision rule?
6. Which failures are terminal-control failures, context-loss failures,
   Provider variance, tool-loop failures, or infrastructure failures?

No report may shorten these answers to "Structure wins" unless every relevant
quality, mechanism, and statistical gate has passed.
