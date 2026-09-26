# Memory-Track Confirmatory Experiment — Freeze Draft, 2026-09-11

> **WITHDRAWN, 2026-09-11. This draft must not be frozen or run.**
>
> Its primary contrast, StructureMemory versus NaiveRAG, is an identity: both
> call `select_lexical_chunks` on the same chunk records with the same `top_k`
> (`benchmarks/baselines/llm_agent.py:86`,
> `benchmarks/adapters/structure_memory.py:313`). With no recorded ratings the
> StructureMemory rating read-path passes every entry through unchanged.
>
> Its sample size was derived from a pilot whose 7 discordant cases all selected
> identical context in both arms (same chunk count, characters and tokens;
> prompts differing by 30-45 tokens of formatting). A second retained run of the
> same design on the same day
> (`benchmark_runs/structure-path-time-20260521-090838/`) reverses the pilot's
> sign. Pooled and deduplicated, 96 LongMemEval cases give a mean difference of
> +0.031, 95% bootstrap CI [-0.031, +0.104], p = 0.366; 99 LoCoMo cases give
> p = 1.000. Both pilots ran on an uncommitted working tree between `cf87f9b`
> and `d4003b0` and match no committed revision.
>
> Its B1 baseline is not full text. The 120,000-char cap keeps the most recent
> ~24% of each LongMemEval-S haystack (`llm_agent.py:96` keeps the tail;
> measured 23.9-24.8% on the section 9 dry run).
>
> No benchmark adapter exercises the paper's mechanism. Glance/overview/detail
> disclosure exists in code (`Context.disclose` in
> `src/structure/models/context/context.py`; `DisclosureLevel` in
> `crates/arabica-protocol`), but every memory adapter passes the full content
> of lexically selected chunks to a `fulltext` reader. `StructurePathMemory`
> adds date/temporal-cue boosts and a `0.001 * session_index` recency tiebreak;
> it does not scan glances or escalate to detail.
>
> A successor must first implement an adapter that exercises the claimed
> mechanism, specified from `paper/sections/method.tex` and declared before any
> scored call, and must size itself without reference to the withdrawn pilot.
> The draft below is retained unchanged for the record.

This pre-registers the confirmatory memory-benchmark experiment that
`paper/sections/experiments.tex` already declares but has never run. It
supersedes nothing; the live long-horizon track remains separately governed by
`context-and-agent-experiment-plan-2026-09-01.md`.

## 1. Motivation and prior evidence

A retained pilot at `benchmark_runs/deepseek-20260521-070721/longmemeval-s-50.json`
compared NaiveRAG and StructureMemory on 50 identical LongMemEval-S cases with
`deepseek-v4-flash`:

| | NaiveRAG | StructureMemory |
|---|---:|---:|
| Score | 0.480 | 0.580 |
| Mean prompt tokens | 17,231 | 17,193 |
| Tokens per scored point | 35,899 | 29,643 (17.4% better) |

Paired outcomes: Structure better on 6 cases, NaiveRAG better on 1, 43 tied.
McNemar z = 1.890, p = 0.0588.

**This pilot is exploratory. It is excluded from every confirmatory estimate**
and is used only to size the sample below.

## 2. Sample size and the winner's-curse guard

Pilot effect estimates from 7 discordant pairs are unstable and biased upward.
The sample is therefore sized to retain power if the true effect is half the
pilot estimate:

| Assumed true effect | psi | Required paired n |
|---|---:|---:|
| pilot effect (delta = 0.100) | 0.140 | 87 |
| 75% of pilot (delta = 0.075) | 0.155 | 171 |
| **half of pilot (delta = 0.050)** | **0.170** | **421** |
| one third of pilot (delta = 0.033) | 0.180 | 1022 |

**Declared sample: the full released corpus for each benchmark, no
subsampling.** This powers the primary contrast down to a halved effect (421
required, 500 available) and removes every sampling decision from the analysis.

| Benchmark | Cases | Detectable delta at psi = 0.15 |
|---|---:|---:|
| LongMemEval-S (cleaned) | 500 | 4.3 pp |
| LongMemEval-V2 | 451 | 4.5 pp |
| LoCoMo | 1,986 QA pairs / 10 conversations | 2.2 pp |

`--sample-mode` and `--sample-percent` are not used. `--max-cases` is not set.

## 3. Frozen parameters

| Parameter | Value |
|---|---|
| Reader model | `deepseek-v4-flash` |
| Provider | DeepSeek Responses API |
| Temperature | 0.0 |
| `--top-k` | 6 |
| `--max-context-chars` | 120000 |
| Methods | `FullText`, `NaiveRAG`, `StructureMemory` |
| Benchmarks | `longmemeval-s`, `longmemeval-v2-small`, `locomo` |
| Scorer | released deterministic benchmark-native scorer; no LLM judge |
| Runner | `benchmarks/scripts/run_full_memory_benchmark.py` |
| Output | `--format json`, per-case diagnostics retained |

DeepSeek is chosen over GLM because its per-token pricing is auditable. The GLM
Coding Plan reported `price_weighting_auditable: false` in every preflight,
which is what blocked price-weighted analysis on the live track.

## 4. Primary hypothesis and decision rule

**Primary contrast:** StructureMemory versus NaiveRAG on LongMemEval-S.

**Primary outcome:** paired per-case benchmark score, analysed by McNemar on
discordant pairs, alpha = 0.05 one-sided, power 0.80 at the declared sample.

**Co-primary reported outcome:** tokens per scored point, reported with a
paired bootstrap 95% interval. It may not be hidden by a favourable accuracy
result, and a favourable accuracy result does not license ignoring it.

**Paper-level decision rule, carried unchanged from
`paper/sections/experiments.tex`:** the claim is supported only if Structure
improves tokens per scored point at comparable accuracy on **at least two of
the three** benchmarks.

This rule is retained deliberately and must not be amended after results are
seen. It is already known that the LoCoMo pilot was flat — 0.4643 versus
0.4647 at identical prompt tokens. **If LongMemEval-S passes and both LoCoMo
and LongMemEval-V2 fail, the claim is not supported and the paper must say so.**
Declaring this in advance is the point of this document.

## 5. Analysis plan

1. Report all three benchmarks, all three methods, with no benchmark dropped
   after the fact.
2. Primary: McNemar on LongMemEval-S StructureMemory vs NaiveRAG.
3. Co-primary: paired bootstrap on tokens per scored point, 10,000 resamples,
   fixed seed, reported as a 95% interval.
4. Secondary contrasts (StructureMemory vs FullText, and the other two
   benchmarks) carry a Holm correction. Report unadjusted effect sizes and
   adjusted decisions separately.
5. Every case is reported. Cases failing for infrastructure reasons are
   retained, counted, and reported separately; they are not silently dropped
   from the denominator.
6. No interim analysis. No stopping for a favourable intermediate estimate.

## 6. Cost envelope

Prompt-token estimate from pilot means, full corpus, per benchmark:

| Methods | LongMemEval-S prompt tokens |
|---|---:|
| NaiveRAG | 8.62 M |
| StructureMemory | 8.60 M |
| FullText | unmeasured in pilot; bounded by the 120,000-char cap |

NaiveRAG plus StructureMemory alone is ~17.2 M prompt tokens for the full 500
cases — against ~0.7 M input tokens for a *single* long-horizon agent trial.

A monetary envelope must be computed from frozen DeepSeek prices and the ECB
reference rate before authorisation, in the same form as
`context-agent-campaign-20260901-v1`. FullText's envelope must be measured by a
dry run before it is authorised, not estimated from the pilot.

## 7. Required artifacts

```
manifest.json        frozen parameters and runner version
freeze.json          revision, dirty digest, checksums, credential env names only
preflight.json       redacted DeepSeek contract probe
ordered-ledger.jsonl every attempt in order, including failures
summary.json         primary, co-primary and secondary results
per-case/            retained per-case diagnostics for every method
```

## 8. Sign-off status

Decided 2026-09-11:

- **Sample: full corpus, no subsampling.** Confirmed. Powers the primary
  contrast against a halved pilot effect.
- **FullText: cost envelope to be measured by a bounded dry run before it is
  authorised for the full corpus.** Confirmed. FullText is not authorised for
  the 500-case run until that measurement exists.

Still open:

1. Confirm `deepseek-v4-flash` as the frozen reader model. Note that the
   shell environment currently configures `BENCHMARK_LLM_MODEL=mimo-v2.5-pro`
   against `token-plan-cn.xiaomimimo.com`. The sample sizing in section 2 is
   derived from a `deepseek-v4-flash` pilot and **does not transfer to a
   different reader model**; switching models requires a new pilot and a
   recomputed sample size.
2. Confirm the monetary cap for this campaign.
3. Confirm that the unchanged two-of-three decision rule is accepted, including
   the real possibility that it returns "claim not supported".

## 9. FullText dry-run record

Purpose: measure the FullText prompt-token envelope, which the pilot never
captured, so that section 6 can carry a real number instead of a bound.

Scope: 3 cases, LongMemEval-S, `deepseek-v4-flash` via the OpenAI-compatible
endpoint, temperature 0, top-k 6, 120,000-char cap. This is a cost
measurement, not a scored trial. **Its accuracy output is noise at n = 3 and
must not enter any confirmatory estimate.**

Measured 2026-09-11:

| Method | Mean prompt tokens/case | Mean completion | Mean latency |
|---|---:|---:|---:|
| FullText | 25,398 | 733 | 5.1 s |
| NaiveRAG | 16,856 | 638 | 4.3 s |

FullText costs **1.51x** NaiveRAG per case, not the multiple of the context cap
that the 120,000-char bound allowed for. Projected full-corpus prompt tokens:

| Scope | 500 cases |
|---|---:|
| FullText | 12.7 M |
| NaiveRAG | 8.4 M |
| StructureMemory (pilot mean) | 8.6 M |
| **All three methods** | **29.7 M** |

**Conclusion: FullText is affordable and is authorised for the full corpus.**
The paper's B1/B2/B3 baseline structure can be kept intact.

### Outstanding price gap

`context-agent-campaign-20260901-v1/manifests/phase-5-manifest.json` records
`price_weighting_auditable: true` and names its source as "DeepSeek official
2026-09-01 USD token prices; ECB 2026-08-31 1 EUR=1.1596 USD", but **does not
carry the numeric per-token rates**. The euro envelope for this campaign
therefore cannot be recomputed from retained artifacts alone. The frozen rate
table must be recorded in this campaign's manifest before authorisation, not
referenced indirectly.
