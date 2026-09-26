# Phase 2 Power Analysis — 2026-09-11

Status: analysis of existing retained data. No Provider calls were made. This
document does not propose any change to a frozen campaign; it estimates whether
the Phase 2 design in `context-and-agent-experiment-plan-2026-09-01.md` can
reach its own declared decision rule.

## Question

Plan section 7.3 declares a primary FBGC/B0 contrast with a quality
non-inferiority margin of 10 percentage points, at sample sizes of:

- minimum: 5 tasks x 5 blocks x 4 arms = 100 trials (25 B0/FBGC pairs);
- target: 5 tasks x 10 blocks x 4 arms = 200 trials (50 B0/FBGC pairs).

Can that design detect a 10 pp difference given the trajectory variance
actually observed?

## Data

Strict success (`verifier_passed`) from the three complete Phase-0 records,
which used the same runner, model, tasks and limits:

- `context-agent-campaign-20260901-v3-system-phase0-01`
- `context-agent-campaign-20260905-v4-auto2-phase0-08`
- `context-agent-campaign-20260908-v5-general-phase0-01`

Retained infrastructure failures are excluded because they are not treatment
outcomes. 45 usable trials remain.

| Arm | Strict success |
|---|---|
| B0 | 12/22 = 0.545 |
| FBGC | 12/23 = 0.522 |

Matching on run, task, block and terminal controller gives 21 B0/FBGC pairs:

| | count |
|---|---|
| both pass | 8 |
| both fail | 6 |
| B0 only | 4 |
| FBGC only | 3 |
| **discordant** | **7 of 21 (psi = 0.333)** |

## Method

Paired binary non-inferiority, McNemar-based:

```
n_pairs = (z_{1-alpha} + z_{1-beta})^2 * psi / delta^2
```

with alpha = 0.05 one-sided, power = 0.80, delta = 0.10, and psi the discordant
proportion. The smallest detectable margin at a given size inverts the same
expression. A Wilson 95% interval on psi propagates its own uncertainty.

## Results

| Estimate source | Pairs | psi | 95% CI psi | Pairs needed | Trials needed |
|---|---:|---:|---|---:|---:|
| all controllers | 21 | 0.333 | [0.172, 0.546] | **207** | **414** |
| | | | CI-implied range | 107-338 | 214-676 |
| advisory only | 10 | 0.400 | [0.168, 0.687] | 248 | 496 |
| | | | CI-implied range | 104-425 | 208-850 |

Smallest non-inferiority margin the planned sizes can actually resolve:

| Design | Pairs | Detectable margin | Declared margin |
|---|---:|---:|---:|
| minimum (100 trials) | 25 | **28.7 pp** | 10 pp |
| target (200 trials) | 50 | **20.3 pp** | 10 pp |

## Implication

The declared Phase 2 design is underpowered for its own decision rule by
roughly a factor of four to eight. At the target size it can only resolve a
margin about twice the one it declares; at the minimum size, nearly three
times.

Two consequences follow.

1. A Phase 2 run at the planned size cannot produce a valid non-inferiority
   conclusion at 10 pp. It can only produce an underpowered result that fails
   to reject, which is not evidence of non-inferiority.
2. The primary contrast alone needs roughly 414 trials. Phase 2 runs a
   four-arm ladder, so a correctly powered version of the full ladder is on
   the order of 828 trials, against a plan whose entire minimum publishable
   program is 266. The Holm correction applied to the secondary component
   family (plan section 7.3) reduces power further and is not modelled here.

This is independent of the terminal-controller problem. Qualifying a
controller does not change psi, so fixing Phase 0 would not make Phase 2
conclusive at the planned size.

## Limitations of this analysis

1. psi is estimated from 21 pairs and its interval is wide. The conclusion is
   robust across that interval — even the optimistic bound requires 107 pairs,
   over four times the planned minimum — but the point estimate should not be
   quoted as precise.
2. Estimates come from Phase-0 data using two tasks (`build-cython-ext`,
   `db-wal-recovery`). Phase 2 declares five tasks. A different task mix would
   change both the success rate and psi.
3. Phase-0 trials mixed two terminal controllers, one known to underperform.
   The advisory-only subset is reported as a robustness check and gives a
   larger requirement, not a smaller one.
4. The normal approximation is used throughout. At these sample sizes an exact
   method would shift the numbers modestly and would not change the conclusion.
5. This analysis addresses quality non-inferiority only. The cost co-primary
   has its own variance, not estimated here.

## Comparison with the memory benchmark — CORRECTED

> **Correction, 2026-09-11 (later the same day). The conclusion of this section
> is withdrawn.** The Phase 2 analysis above is unaffected.
>
> 1. **The pilot compared a method with itself.** `StructureMemory` selects
>    context with `select_lexical_chunks(question, case_chunk_records(case),
>    top_k)`, the same function `NaiveRAG` calls
>    (`benchmarks/baselines/llm_agent.py:86`,
>    `benchmarks/adapters/structure_memory.py:313`). On all 7 discordant pilot
>    cases both arms selected identical context; prompts differed by 30-45
>    tokens of formatting. The pilot's +10 pp is answer variance on the same
>    input.
> 2. **A second retained run reverses it.**
>    `benchmark_runs/structure-path-time-20260521-090838/` (same model, same
>    day, a different hash sample sharing 4 of 50 cases) gives StructureMemory
>    0.40 vs NaiveRAG 0.44. Pooled and deduplicated by case: 96 LongMemEval
>    cases, Structure better on 7 and worse on 4, mean +0.031, 95% bootstrap CI
>    [-0.031, +0.104], p = 0.366. LoCoMo, 99 cases: 24 vs 24, p = 1.000.
> 3. **No benchmark adapter exercises the paper's mechanism.** Glance/overview/
>    detail disclosure exists in code (`Context.disclose`;
>    `DisclosureLevel` in `crates/arabica-protocol`) but is not called by any
>    memory adapter.
>
> The "87 cases" requirement below is therefore void: it was derived from an
> effect between two identical selectors. What survives is the cost fact — a
> memory-benchmark observation costs roughly a fortieth of a long-horizon agent
> trial — but no currently implemented method has a contrast that would test
> the paper's claim. The original text follows, retained for the record.

### Original text (withdrawn)

The same calculation applied to the memory-benchmark track gives a very
different answer. A retained paired run at
`benchmark_runs/deepseek-20260521-070721/longmemeval-s-50.json`
(LongMemEval-S, 50 cases, `deepseek-v4-flash`, NaiveRAG vs StructureMemory on
identical cases) gives:

| | NaiveRAG | StructureMemory |
|---|---:|---:|
| Score | 0.480 | 0.580 |
| Mean prompt tokens | 17,231 | 17,193 |
| Tokens per scored point | 35,899 | **29,643** (17.4% better) |

Paired per-case outcomes: Structure better on 6, NaiveRAG better on 1, 43 tied.
Discordance psi = 0.140, McNemar z = 1.890, p = 0.0588 — a favourable effect
that does not reach significance at this sample size.

Required sample at the observed effect (delta = 0.100, psi = 0.140, alpha =
0.05 one-sided, power = 0.80): **87 cases**. The released LongMemEval corpora
contain **500** cases.

| Track | Unit cost | Needed | Available | Reachable? |
|---|---|---:|---:|---|
| Phase 2 live ladder | ~700k input tokens/trial | 414 trials | budget ~266 | No |
| LongMemEval | ~17k prompt tokens/case | 87 cases | 500 | **Yes** |

The memory track needs roughly a fortieth of the tokens per observation and
has six times the required sample already available. It is the only one of the
two tracks whose declared decision rule is reachable within the existing
budget.

LoCoMo does not show the same effect: 0.4643 versus 0.4647 at identical prompt
tokens, i.e. flat. The plan's "at least two of three benchmarks" rule therefore
is not met by LongMemEval alone, and LongMemEval-V2 has not been run.

### Consequence for sequencing

The pilot above is a pilot. Using it to estimate an effect size and then
freezing a sample is legitimate; running incrementally and stopping when p
crosses 0.05 is not. Any confirmatory run must declare its case count,
sampling rule, reader model, temperature, scorer and analysis before the first
scored call, exactly as section 2 of the experiment plan requires for the
live track.

## Reproduction

Deterministic and LLM-free. Inputs are the three retained Phase-0 ledgers
listed above and the retained LongMemEval pilot; no network access is
required.
