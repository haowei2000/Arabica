# Long-Task B0 Baseline — 2026-07-29

## Result

Three sequential B0 attempts were completed on the Terminal-Bench 2.0
`llm-inference-batching-scheduler` task with LongCat-2.0. All three received
reward 0. Two exhausted the 1,800-second agent budget; the third stopped after
five Provider calls because the final response hit `finish_reason=length`.

The attempts are useful as an operational B0 baseline, but they are not yet a
paper-quality B0/PGC comparison:

- the three B0 verifier outcomes varied from 5/6 to 1/6;
- the existing PGC arm has only one attempt;
- B0 and PGC were not interleaved;
- the B0 and PGC frozen manifests have different runtime source hashes;
- no attempt passed the complete task-quality gate.

The strongest result is therefore conditional: for the B0 and PGC attempts
that reached the same 5/6 verifier endpoint and the same failing bucket-1
cost, PGC reduced cumulative Provider input tokens by 76.6%, but increased
uncached input tokens by 53.9%. This demonstrates context-size reduction, not
lower uncached-token cost or task-quality non-inferiority.

## Configuration

- Dataset: Terminal-Bench 2.0
- Dataset commit: `69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`
- Task: `llm-inference-batching-scheduler`
- Provider/model: LongCat OpenAI-compatible API, `LongCat-2.0`
- Strategy: B0 full replay
- Attempts/concurrency: three / one
- Agent timeout: 1,800 seconds
- Maximum model calls: 128
- Maximum output tokens per call: 8,192
- Frozen binary: `target/harbor-scaffolds/long-b0-file-baseline-v4/harbor_agent`
- Binary SHA-256:
  `5d5b2f73904ed3ebb4f080be6ed03ee81244bfd3fb3b65f676fe4c1f748600ca`
- Runtime source SHA-256:
  `75d40f12cebd874603fc42f652b876b607ac40495a81a0cf0dc9948a38f121c5`

The full source and configuration hashes are recorded in
`target/harbor-scaffolds/long-b0-file-baseline-v4/manifest.txt`.

## Attempt results

| Trial | Calls | Input | Cached | Uncached | Output | Agent result | Verifier |
|---|---:|---:|---:|---:|---:|---|---:|
| `v82X4cz` | 66 | 3,010,191 | 2,903,936 | 106,255 | 80,026 | timeout | 5/6 |
| `oZ4NYD6` | 75 | 4,610,227 | 4,473,216 | 137,011 | 79,159 | timeout | 1/6 |
| `qjjtAH4` | 5 | 27,402 | 14,976 | 12,426 | 8,986 | truncated, misclassified as terminal success | 1/6 |
| **Total** | **146** | **7,647,820** | **7,392,128** | **255,692** | **168,171** | 2 timeouts | 0/3 reward |

The two full-duration attempts averaged 70.5 Provider calls, 3,810,209 input
tokens, 3,688,576 cached tokens, 121,633 uncached tokens, and 79,592.5 output
tokens. Their cache-hit rate was 96.8%. The third attempt must not be mixed
into a full-duration efficiency mean because it ended after about 219 seconds.

The first attempt produced both required files and passed schema, integrity,
shape, and coverage checks. It failed only the performance gate: bucket-1
cost was `2,483,023,679,002`, above the `3.0e11` threshold. The second and
third attempts produced neither required plan file, so only the immutable
input-data check passed.

## Same-call-count slice

The PGC sample ended at 58 Provider calls. Slicing both full-duration B0
traces at the same call count avoids conflating context growth with a different
number of model turns:

| Strategy/trial | Calls | Input | Cached | Uncached | Output |
|---|---:|---:|---:|---:|---:|
| B0 `v82X4cz` | 58 | 2,301,317 | 2,206,336 | 94,981 | 71,411 |
| B0 `oZ4NYD6` | 58 | 2,636,379 | 2,530,688 | 105,691 | 59,142 |
| PGC `Gga4vg5` | 58 | 703,994 | 540,416 | 163,578 | 74,051 |

At a fixed 58 calls, PGC transmitted far less repeated context but paid more
uncached input. The two B0 trajectories also differ materially, confirming
that model-trajectory variance must be measured rather than inferred from one
run.

## Same-quality endpoint comparison

B0 `v82X4cz` and PGC `Gga4vg5` both passed exactly five of six verifier tests.
Both failed the performance test at the same bucket-1 cost,
`2,483,023,679,002`. Relative to that B0 attempt, PGC changed the observed
economics as follows:

| Metric | B0 | PGC | PGC change |
|---|---:|---:|---:|
| Provider calls | 66 | 58 | -12.1% |
| Input tokens | 3,010,191 | 703,994 | -76.6% |
| Cached input tokens | 2,903,936 | 540,416 | -81.4% |
| Uncached input tokens | 106,255 | 163,578 | +53.9% |
| Output tokens | 80,026 | 74,051 | -7.5% |
| Verifier | 5/6 | 5/6 | same incomplete endpoint |

This is evidence that PointerGC can reduce total model-visible context on a
real long trajectory. It is not evidence of lower Provider cost when
uncached tokens are the dominant price, and it is not a causal task-quality
claim. The matching verifier failure is also the provided slow baseline's
performance result, not successful completion of the optimization task.

For additional orientation, the single PGC timeout used 81.5% less total
input than the mean of the two B0 timeouts, while using 34.5% more uncached
input. Because the PGC arm has `n=1` and B0 quality varied, that aggregate is
descriptive only.

## Benchmark reliability defect

Trial `qjjtAH4` exposed a termination-classification defect. Its fifth response
contained no response items, no final output, and `finish_reason=length` after
emitting the full 8,192-token output allowance. The runtime currently treats
every response without a tool call as `RunCompleted`, irrespective of finish
reason. The Harbor report then defines `terminal_success` as the presence of a
`RunCompleted` event and therefore recorded this truncated run as successful.

This affected both production semantics and benchmark validity. It was fixed
on 2026-07-30 before running another paid comparison. Runtime now rejects
`length` as `RunFailed` with the stable `model_output_truncated:` prefix even
when partial visible text exists. It also rejects content-filter termination,
empty terminal output, and inconsistent finish-reason/tool-item pairs. Session
Management records the run as `Failed`, while Harbor independently requires a
non-empty `RunCompleted.output` before setting `terminal_success=true`.

Focused Runtime, Session, and Harbor-report regression tests cover the full
classification path. Provider and tool raw bodies are retained separately, so
a future truncated response remains available for diagnosis even though it is
not admitted as a successful terminal answer.

## Next experiment

Freeze one post-fix binary and use it for both arms.
Run an interleaved sequence such as `B0, PGC, B0, PGC, B0, PGC`, with identical
task, model, maximum steps, token allowance, timeout, and archive root policy.
Report three distinct views:

1. all attempts, including timeouts and truncations;
2. matched-duration or matched-call-count efficiency;
3. success-conditioned efficiency only after both arms pass the complete
   verifier gate.

The primary economic outcome should be uncached input tokens. Total input,
cache-hit tokens, wall time, model calls, task reward, and all verifier checks
remain mandatory secondary outcomes. The PointerGC arm must additionally
report admissions, cache resets, pointer appearances, and whether archived
evidence was actually re-read into the model loop.

## Alternative task progression

The next comparison should not depend only on the inference-scheduler task.
The following Terminal-Bench tasks were inspected on 2026-07-30 and are
ordered for the next experiments:

1. `build-cython-ext`: preferred first task. Compile, diagnose, patch, and
   retest cycles should create a natural medium-to-long tool trajectory while
   retaining an executable success gate.
2. `configure-git-webserver`: a system-configuration task with a concrete
   end-to-end verifier and a more stable success target.
3. `query-optimize`: a data-rich iterative task likely to stress context, but
   every database inspection must use the existing safety snapshot and an
   immutable or copied database because the previous attempt modified input
   state.
4. `fix-code-vulnerability`: a later high-pressure long task. Its declared
   expert estimate exceeds the 15-minute agent budget, so it is not suitable
   for establishing the first paired success baseline.

All new Harbor runs must use report schema v4 raw capture. Each Provider call
retains the exact sent JSON body and exact received HTTP body under
`provider-raw/`; each shell call retains its complete command, stdout, and
stderr under `tool-raw/` before the model-facing event is truncated. Raw
artifacts never contain the Authorization header, remain private under the
trial directory, and must not be committed.

## Artifacts

- `target/harbor-terminal-bench/structure-llm-scheduler-b0-baseline-3x-v4`
- `target/harbor-scaffolds/long-b0-file-baseline-v4/manifest.txt`
- `target/harbor-terminal-bench/structure-llm-scheduler-prob-pgc-smoke-2`
- `target/harbor-scaffolds/long-probabilistic-pgc-v3/manifest.txt`

Harbor artifacts and credentials remain outside version control. This report
contains no API key or authorization header.
