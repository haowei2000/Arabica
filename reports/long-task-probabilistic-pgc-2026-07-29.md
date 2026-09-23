# Long-Task Probability-Weighted PointerGC Study — 2026-07-29

## Result

The runtime now treats `PGC_EFFORT` as an uncached-token return gate rather
than a checkpoint-frequency knob:

```text
estimated_saved_tokens_per_call × probability_weighted_remaining_calls
    >= estimated_cache_reset_tokens × PGC_EFFORT
```

The remaining-call estimate is a deterministic geometric survival sum. With
the experiment's continuation probability of 7,500 basis points and a large
step budget, its limit is approximately 3.996 calls. Removable bytes are
converted to tokens with the preceding real request's observed token density.
When Provider usage is available, reset cost is the preceding response's
`cached_input_tokens`; the projected stable-prefix size is used only before a
real usage observation exists.

The long-task smoke demonstrates the intended mechanism: GC repeatedly fires
in the middle of a task, invalidates or reduces cache once, and is followed by
several calls that reuse the smaller context epoch. A subsequent three-attempt
B0 baseline provides descriptive comparison data, but still does **not** prove
end-to-end uncached-token savings or task-quality non-inferiority. The PGC
sample timed out after 1,800 seconds with 58 successful Provider calls and
failed one of six verifier checks. A same-binary, interleaved, repeated B0/PGC
experiment is still required; see
`reports/llm-scheduler-b0-baseline-2026-07-29.md`.

## Implementation

The Rust runtime now records per-run Provider economics:

- previous provider-visible request bytes;
- previous input tokens;
- previous cached input tokens.

Each eligible decision emits a `PointerGcAdmissionObservation` containing the
estimated per-call saving, reset cost, probability-weighted horizon, source of
the reset measurement, and final admission. Harbor writes both Provider calls
and admissions incrementally, so an external timeout preserves the causal
trace. Archived epochs are immutable: once an epoch is represented by
recoverable pointers, later gate rejection cannot expand it back to full
events and disturb the prefix again.

Configuration:

- `PGC_EFFORT`: required return multiple, positive integer;
- `PGC_CONTINUATION_PROBABILITY_BPS`: per-step continuation probability,
  0–10,000 basis points, default 7,500;
- checkpoint size: four eligible batches in these long-task smokes;
- maximum model calls: 128.

Validation passed:

- `cargo fmt --all --check`;
- `cargo test --workspace` (including the local mock HTTP test outside the
  filesystem/network sandbox);
- `cargo clippy --workspace --all-targets -- -D warnings`;
- Python Harbor bridge compilation.

The runtime test suite includes actual Provider-cache selection, zero-cache
measurements, missing-usage fallback, probability/budget bounds, effort
monotonicity, and immutable committed-pointer behavior.

## Frozen long-task sample

- Dataset: Terminal-Bench 2.0
- Task: `llm-inference-batching-scheduler`
- Provider/model: LongCat OpenAI-compatible API, `LongCat-2.0`
- Strategy: PGC
- `max_tokens=8192`
- `checkpoint_batches=4`
- `PGC_EFFORT=1`
- `PGC_CONTINUATION_PROBABILITY_BPS=7500`
- Attempts/concurrency: one / one
- Frozen binary:
  `target/harbor-scaffolds/long-probabilistic-pgc-v3/harbor_agent`
- Binary SHA-256:
  `fc9c68c6ea5859dce7c0c94038837ada38971c42bac9130c9bacfcc4cee7d8f1`
- Dataset source commit:
  `69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`

The complete configuration and source hashes are in
`target/harbor-scaffolds/long-probabilistic-pgc-v3/manifest.txt`.

## Four-objective results

| Objective | Result | Interpretation |
|---|---:|---|
| Task utility | reward 0; 5/6 verifier checks passed | Files, integrity, schema, feasibility, and coverage passed; performance threshold failed. The agent was externally timed out while still issuing tool calls. |
| Token/cache economic cost | 703,994 input; 540,416 cached; 163,578 uncached; 74,051 output; 76.8% cache hit | This is a long mechanism-active trace, not evidence of savings without a matched B0 trajectory. |
| Interaction complexity | 58 successful Provider calls; all ended in `tool_calls`; 55 admission checks; 9 admissions | The model never emitted terminal `stop` before timeout. Pointer appearances accumulated to 1,328 across calls. |
| Time | 1,800s agent timeout; 1,776.6s summed Provider latency | Provider latency accounts for about 98.7% of the agent limit; Runtime GC is not the observed time bottleneck. |

Peak input was 26,105 tokens and peak serialized model input was 80,857 bytes.
There were five observed cached-to-zero transitions. The official verifier
reported bucket 1 cost `2.483e12`, above the `3.0e11` threshold; the other five
tests passed.

## Admission and reuse trace

| Step | New batches | Estimated saved/call | Measured reset | Calls before next GC/end | First-call cache | Last-call cache |
|---:|---:|---:|---:|---:|---:|---:|
| 12 | 8 | 4,605 | 8,448 | 8 | 1,792 / 6,323 | 10,624 / 12,237 |
| 20 | 8 | 4,065 | 10,624 | 4 | 1,792 / 8,960 | 11,648 / 12,925 |
| 24 | 4 | 3,460 | 11,648 | 8 | 1,792 / 11,444 | 25,216 / 26,105 |
| 32 | 8 | 10,577 | 25,216 | 4 | 0 / 13,865 | 22,656 / 23,359 |
| 36 | 4 | 8,970 | 22,656 | 4 | 0 / 14,243 | 15,360 / 16,048 |
| 40 | 4 | 5,338 | 15,360 | 7 | 512 / 6,014 | 1,792 / 14,708 |
| 47 | 4 | 1,707 | 1,792 | 1 | 1,792 / 13,528 | 1,792 / 13,528 |
| 48 | 4 | 1,592 | 1,792 | 8 | 0 / 11,714 | 17,024 / 17,814 |
| 56 | 8 | 7,460 | 17,024 | 3 before timeout | 0 / 8,197 | 9,216 / 10,318 |

The first six admissions give direct evidence of mid-task amortization: cache
recovers after the context-epoch rewrite and the smaller epoch is reused. The
step-47/48 pair exposes a limitation. Cache had already fallen from 8,320 to
1,792 at step 46 without a pointer transition, so two subsequent checks saw a
cheap reset and admitted consecutive collections. This may be the correct
marginal decision while the cache is already cold, but it also shows that a
single preceding `cached_input_tokens` observation is noisy.

## Comparison with the preceding smoke

The previous frozen v2 sample completed in 45 Provider calls, passed five of
six verifier checks, and admitted three GC transitions at steps 8, 24, and 36.
Its older reset estimator used the greater of cached tokens and projected
prefix tokens, producing reset estimates of 14,059, 42,949, and 68,762 despite
observed cached counts of 8,064, 24,832, and 32,512. Inspection of rejected
checks showed that this delayed profitable GC around steps 19 and 33.

The corrected v3 trace admitted at steps 12, 20, 24, 32, 36, 40, 47, 48, and
56. Steps 20 and 32 confirm that using actual Provider cache cost can move GC
earlier and leave a longer reuse interval. However, v2 and v3 are independent
stochastic model trajectories, not a paired counterfactual: their aggregate
tokens, steps, time, and verifier quality must not be used as a causal ranking.

## What is established

1. Probability-weighted remaining calls and observed uncached reset cost are
   now real runtime admission inputs, not post-hoc benchmark calculations.
2. GC can activate in the middle of a real long task and retain exact,
   recoverable evidence through file/SQLite-backed pointers.
3. Context-epoch rewrites cause bounded cache disruption followed by reuse;
   committed pointer epochs do not oscillate back to full history.
4. A 75% geometric continuation prior produces an effective horizon of about
   four calls and is selective when cache value is high.

## What is not established

1. A three-attempt B0 baseline now exists, but it is neither the same stochastic
   trajectory nor the same frozen runtime hash, so causal net-token savings are
   not proven.
2. One PGC sample is insufficient for task-quality non-inferiority.
3. The task timed out and failed its optimization target, so PGC has not passed
   the task-utility gate.
4. A single cached-token observation can be distorted by Provider cache
   eviction or prefix changes unrelated to GC.

## Next experiment

Keep the probability-weighted gate, then evaluate two reset-cost estimators:

1. **Last observation:** the implemented v3 behavior.
2. **Robust short window:** a three-to-five-call median or conservative EMA of
   cached prefix value, with an explicit rule for an already-cold cache.

Add a checkpoint coalescing/cooldown ablation so consecutive admissions such
as steps 47 and 48 can be compared with one combined epoch switch. Do not add a
blind fixed delay: a genuinely cold cache is the cheapest moment to collect.

For causal evidence, run interleaved B0 and PGC attempts on the same official
long-task family, at least three attempts per arm for smoke and more for paper
claims. Report all attempts and success-conditioned efficiency separately.
Advance to a larger matrix only after both arms produce at least one completed,
verifier-passing sample. The decisive quantity is observed cumulative
uncached-token cost after each switch, not pointer count or nominal strategy.

## Artifacts

- `target/harbor-terminal-bench/structure-llm-scheduler-prob-pgc-smoke-2`
- `target/harbor-scaffolds/long-probabilistic-pgc-v3/manifest.txt`
- `target/harbor-terminal-bench/structure-llm-scheduler-prob-pgc-smoke-1`
- `target/harbor-scaffolds/long-probabilistic-pgc-v2/manifest.txt`

Harbor artifacts and credentials remain outside version control. The report
contains no API key or authorization header.
