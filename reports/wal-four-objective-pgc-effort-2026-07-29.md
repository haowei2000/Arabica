# WAL Four-Objective PGC Effort Study — 2026-07-29

## Result

This smoke study organizes context management evaluation around four objectives:

1. **Task utility**: official verifier success and failure taxonomy.
2. **Token/cache economic cost**: input, output, uncached input, cache reuse,
   reset cost, and peak context.
3. **Interaction complexity**: Provider calls, tool calls, errors, and retrieval.
4. **Latency**: agent elapsed time and Provider latency.

Task utility and protocol safety are constraints. Economic cost, steps, and
latency are compared only after those constraints are met; they are not merged
into a single score. Cache is part of economic cost rather than a fifth
objective because a high cache-hit ratio can be obtained by repeatedly sending
an unnecessarily large history.

The experiment does **not** demonstrate token savings from PointerGC. The only
trial in which the cache-aware gate admitted GC switched context epochs on the
final Provider call. It reduced that call's input but invalidated more cached
tokens than it saved, and there were no later calls over which to amortize the
reset. `PGC_EFFORT=4` correctly declined every rewrite in this WAL sample and
therefore behaved as a cache-first full-history policy.

## Frozen setup

- Dataset/task: Terminal-Bench 2.0 `db-wal-recovery`
- Provider/model: LongCat OpenAI-compatible API, `LongCat-2.0`
- Generation bound: `max_tokens=8192`
- Agent bound: 128 Provider calls
- Pointer checkpoint: eight eligible closed batches
- Attempts: three per arm, sequential (`n_concurrent=1`)
- Arms: B0, PGC effort 1, PGC effort 2, PGC effort 4
- Report schema: `structure.harbor-agent/v2`
- Frozen binary:
  `target/harbor-scaffolds/wal-four-objective-v2/harbor_agent`
- Binary SHA-256:
  `5323835bbe5d889063d21915eb1904b5a53d66b9361cce87cbbea26f915f45c6`

The binary, Rust agent, Python bridge, runtime, and lockfile hashes still match
`target/harbor-scaffolds/wal-four-objective-v2/manifest.txt` after all 12
trials. Credentials were injected from the private environment file and are
not present in reports or artifacts committed to the repository.

## All-attempt results

Means include failed attempts. `Cache hit` is aggregate cached input divided by
aggregate input. `Reset` counts Provider-observed cached-to-zero transitions;
`pointer reset` restricts that count to a transition that also introduces
pointer entries. Time is the Rust agent phase, not Harbor image setup or the
official verifier.

| Arm | Pass | Length failures | Mean input | Mean uncached | Cache hit | Mean output | Mean peak input | Mean calls | Mean tools | Mean agent s | Pointer-active trials | Pointer resets |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B0 | 2/3 | 1 | 39,827 | 6,632 | 83.3% | 5,856 | 5,005 | 12.7 | 11.7 | 181.8 | 0/3 | 0 |
| PGC effort 1 | 2/3 | 1 | 35,461 | 7,728 | 78.2% | 12,108 | 5,131 | 11.0 | 10.0 | 291.1 | 1/3 | 1 |
| PGC effort 2 | 0/3 | 3 | 29,576 | 5,512 | 81.4% | 12,380 | 5,772 | 9.3 | 8.3 | 264.3 | 0/3 | 0 |
| PGC effort 4 | 2/3 | 1 | 43,984 | 6,395 | 85.5% | 6,763 | 6,301 | 13.3 | 12.3 | 160.4 | 0/3 | 0 |

Median agent times were 181.7s, 349.7s, 298.8s, and 177.2s respectively.
P95 is intentionally not estimated from only three repetitions; the per-trial
table below preserves the complete small-sample distribution.

The lower all-attempt mean input for effort 1 and effort 2 is not an efficiency
result. Each arm contains short failed trajectories, and all five failures in
the matrix ended with `finish_reason=length` after LongCat emitted the full
8,192-token allowance. Effort 2's three failures all occurred before pointer
activation. No trial had a Provider exception, archive failure, retrieval
failure, or tool call/result conformance failure.

## Success-conditioned efficiency

| Arm | Successful n | Mean input | Mean uncached | Cache hit | Mean output | Mean peak input | Mean calls | Mean tools | Mean agent s | Pointer-active |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B0 | 2 | 37,307 | 6,779 | 81.8% | 4,491 | 4,657 | 13.0 | 12.0 | 183.6 | 0/2 |
| PGC effort 1 | 2 | 49,178 | 10,202 | 79.3% | 13,875 | 6,399 | 14.0 | 13.0 | 355.7 | 1/2 |
| PGC effort 4 | 2 | 55,948 | 6,476 | 88.4% | 5,867 | 6,044 | 17.0 | 16.0 | 152.1 | 0/2 |

Effort 2 has no successful sample and does not pass the quality gate. Among
successful samples, effort 1 consumed more total and uncached input than B0.
Effort 4 consumed slightly less uncached input than B0 but much more total input
and more steps; its higher cache hit is therefore an explanatory mechanism,
not an overall economic win. Its lower elapsed time despite more steps comes
from shorter/faster model generations and demonstrates why steps and latency
must remain separate objectives.

## Per-trial evidence

| Arm | Trial | Reward | Input | Uncached | Output | Calls | Tools | Pointer appearances | Pointer reset | Agent s | Final reason |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| B0 | `347dFwb` | 1 | 39,197 | 5,789 | 6,604 | 13 | 12 | 0 | 0 | 181.7 | stop |
| B0 | `XkGUxL5` | 1 | 35,417 | 7,769 | 2,377 | 13 | 12 | 0 | 0 | 185.5 | stop |
| B0 | `kpBEYpt` | 0 | 44,866 | 6,338 | 8,587 | 12 | 11 | 0 | 0 | 178.3 | length |
| PGC e1 | `LA5qWTT` | 1 | 49,793 | 9,729 | 14,575 | 16 | 15 | 8 | 1 | 361.8 | stop |
| PGC e1 | `Pky6H2L` | 0 | 8,028 | 2,780 | 8,574 | 5 | 4 | 0 | 0 | 161.9 | length |
| PGC e1 | `ksoZhio` | 1 | 48,563 | 10,675 | 13,175 | 12 | 11 | 0 | 0 | 349.7 | stop |
| PGC e2 | `UFfoZnp` | 0 | 19,632 | 6,064 | 8,742 | 7 | 6 | 0 | 0 | 187.6 | length |
| PGC e2 | `fBzLU6f` | 0 | 27,704 | 4,280 | 14,789 | 10 | 9 | 0 | 0 | 306.5 | length |
| PGC e2 | `hMio63M` | 0 | 41,391 | 6,191 | 13,610 | 11 | 10 | 0 | 0 | 298.8 | length |
| PGC e4 | `BUVWj3m` | 1 | 43,799 | 6,167 | 1,637 | 16 | 15 | 0 | 0 | 62.0 | stop |
| PGC e4 | `cSVxG6s` | 0 | 20,058 | 6,234 | 8,555 | 6 | 5 | 0 | 0 | 177.2 | length |
| PGC e4 | `jxfvpxD` | 1 | 68,096 | 6,784 | 10,096 | 18 | 17 | 0 | 0 | 242.1 | stop |

No trial issued `memory_search` or `memory_read`. Tool errors were ordinary
failed diagnostic commands: three in B0, two in effort 1, four in effort 2,
and one in effort 4.

## Mechanism-active trace

`LA5qWTT` is the only mechanism-active sample. Calls 1–15 retained the full
history. On call 16, the gate admitted eight pointer appearances:

| Metric | Call 15 | Call 16 | Change |
|---|---:|---:|---:|
| Model input tokens | 5,383 | 3,447 | -1,936 (-36.0%) |
| Cached input tokens | 5,120 | 0 | -5,120 |
| Uncached input tokens | 263 | 3,447 | +3,184 |
| Model input bytes | 16,059 | 14,135 | -1,924 (-12.0%) |
| Projected run-memory bytes | 10,636 | 7,781 | -2,855 (-26.8%) |

The task stopped successfully on call 16, so the actual number of future calls
that reused the smaller view was zero. The gate used `checkpoint_batches=8` as
its expected reuse horizon and therefore assumed savings that never occurred.
The trial proves that PointerGC can shrink a real Provider input without losing
task correctness, but it also falsifies the current horizon estimator for this
short task.

## Four-objective interpretation

1. **Task utility:** B0, effort 1, and effort 4 each passed 2/3; effort 2 passed
   0/3. With only three repetitions and five pre-GC model-length failures,
   these rates are smoke evidence, not a strategy ranking.
2. **Economic cost:** no PGC arm dominates B0. Effort 1's active rewrite paid a
   cache reset on the last call. Effort 4 preserved the best cache-hit share but
   replayed more total tokens on its longer successful trajectories.
3. **Steps:** successful effort 4 samples needed 17 Provider calls on average,
   versus 13 for B0. This is stochastic trajectory variation, since effort 4
   never changed the context view.
4. **Latency:** Provider latency accounts for almost all measured agent time.
   GC/archive overhead is below the resolution of this run; model reasoning
   length dominates variance.

There is no Pareto winner. B0 is the current reference. Effort 4 is the safest
default among the tested PGC settings because it avoids an uneconomic rewrite,
but on WAL it is operationally a no-op and cannot demonstrate PointerGC value.

## Next experiment

The next change should improve admission rather than make compression more
aggressive:

1. Replace the fixed `checkpoint_batches` reuse assumption with a
   probability-weighted remaining-horizon estimate based on task progress and
   recent step behavior.
2. Compute break-even using **uncached-token cost**, not only removable bytes:
   expected future per-call savings must cover the observed/predicted cache
   reset plus pointer/checkpoint overhead.
3. Require at least two predicted post-switch Provider calls, or suppress a
   context-epoch switch when the model signals final verification/completion.
4. Add projection, archive, and hydration timing fields so Runtime overhead can
   be separated from Provider latency.
5. Run a longer task family where GC activates early enough to have a real
   amortization horizon. Interleave B0 and effort arms and increase repetitions
   before making a paper claim.

The main hypothesis for the next study is therefore: **cache-aware PointerGC
helps only when the estimator can identify a sufficiently long remaining
horizon; on short tasks it should deliberately collapse to Full Replay.**

## Artifacts

- `target/harbor-terminal-bench/structure-wal-4obj-b0-3x`
- `target/harbor-terminal-bench/structure-wal-4obj-pgc-e1-3x`
- `target/harbor-terminal-bench/structure-wal-4obj-pgc-e2-3x`
- `target/harbor-terminal-bench/structure-wal-4obj-pgc-e4-3x`
- `target/harbor-scaffolds/wal-four-objective-v2/manifest.txt`

Harbor job artifacts are intentionally ignored by Git; this report preserves
the frozen configuration, aggregate measurements, causal trace, and limits.
