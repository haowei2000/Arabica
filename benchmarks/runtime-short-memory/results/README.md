# Tier-B Result Artifacts

This directory contains credential-free benchmark reports produced by the
Rust-native Tier-B harness. An artifact is evidence only at the level declared
by its `evidence_level` field.

## 2026-07-26 LongCat live smoke

- artifact: `2026-07-26-longcat-2.0-live-smoke.json`
- evidence level: `live_api`
- provider wire protocol: OpenAI Chat Completions
- endpoint: `https://api.longcat.chat/openai/v1`
- model: `LongCat-2.0`
- source revision: `2b08c163eebb55d522a80f56816f3f92e16530bd`
- source worktree: clean
- result: 1/1 task passed with an exact file-content match
- scope: one engineering smoke task, not a policy comparison or paper result

The API key was injected only into the benchmark process. It is not present in
the artifact or repository.

## 2026-07-31 B0 WAL baseline

- artifact: `2026-07-31-b0-wal-baseline.json`
- evidence level: `live_api`
- task: Terminal-Bench 2.0 `db-wal-recovery`
- model: `LongCat-2.0`
- strategy: B0 full replay, no context compaction
- frozen binary SHA-256: `1cd20b3b024de95ddc52a8b28610b4d5515f16e4b4c217e7bb5f4fd27e5ba619`
- result: 3/5 attempts passed all 7 verifier checks
- accepted-sample mean: 45,940 input tokens, 13,428 uncached input
  tokens, and 109.0 seconds wall time
- scope: success-rate and success-conditioned cost baseline for later matched
  B0/FBGC trials; it is not evidence of an FBGC treatment effect

The artifact retains both rejected length-truncated attempts so later analyses
cannot silently select only successful trajectories. Provider and tool raw
captures were checked for completeness; authorization headers and credentials
are not stored.

## 2026-07-31 matched FBGC WAL arm

- artifact: `2026-07-31-fbgc-wal-matched.json`
- evidence level: `live_api`
- task, model, binary, generation limits, and concurrency match the B0 WAL
  baseline above
- strategy: lossless file-backed GC with checkpoint size 8, effort 1, and
  continuation probability 7,500 basis points
- result: 3/5 attempts passed all 7 verifier checks, matching the observed B0
  success rate
- accepted-sample mean: 40,172 input tokens, 13,420 uncached input tokens,
  66.59% aggregate cache ratio, and 102.1 seconds wall time
- GC behavior: only 1/5 attempts admitted a transition; it archived 8 batches
  and passed the verifier, but the transition occurred immediately before the
  terminal model call and no archive read or later reuse interval occurred
- scope: evidence that the file-backed transition can preserve task quality in
  one live trial, not evidence that GC reduces end-to-end token cost

The two rejected attempts ended with Provider `finish_reason=length` before any
GC admission. Relative to accepted B0 samples, accepted FBGC samples used 12.6%
fewer total input tokens but essentially identical uncached input tokens
(-0.06%) and a 4.18 percentage-point lower cache ratio. These are descriptive,
unpaired small-sample observations rather than a treatment-effect estimate.

## 2026-07-31 FBGC Cython GC mechanism-gate sample

- artifact: `2026-07-31-fbgc-cython-long-gate-probes.json`
- evidence level: `live_api`
- task: Terminal-Bench 2.0 `build-cython-ext`
- frozen binary SHA-256:
  `fbdd1749eb0b0f1f9cc04e3a80e937b39ed4c200de207edd3c537903e2545ca5`
- automatic trajectory gate: first GC by 60% of Provider calls, at least four
  post-GC Provider calls, and a later exact archive hydration
- gate result: 2/2 probes passed; first GC occurred at step 6, followed by
  44--45 Provider calls, and each probe hydrated about 10.9 KB from an archive
- sample role: frozen GC mechanism-gate evidence; exclude it from the pool of
  successful task-quality samples
- task result: 0/2 strictly accepted; both reached Runtime terminal success but
  scored 10/11 because `ccomplexity.pyx` retained the removed NumPy alias
  `np.int`
- scope: evidence that FBGC can trigger early, sustain a long reuse interval,
  and recover full archived content; not yet a successful end-to-end quality
  sample or a B0/FBGC cost comparison

The strict acceptance rule combines Runtime terminal success, Harbor reward 1,
all verifier checks, and the GC trajectory gate. Passing the trajectory gate
alone is never treated as a successful benchmark result.

## 2026-07-31 FBGC quality-sample search

- artifact: `2026-07-31-fbgc-quality-sample-search.json`
- evidence level: `live_api`
- frozen binary SHA-256:
  `fbdd1749eb0b0f1f9cc04e3a80e937b39ed4c200de207edd3c537903e2545ca5`
- search result: 10 attempts, two task-quality successes, one complete GC-gate
  pass, and zero strictly accepted samples
- stable quality result: two `db-wal-recovery` trials passed all 7 checks, but
  one admitted GC at step 9/10 with no reread and the other admitted no GC
- long-task result: `fix-code-vulnerability` passed the full GC trajectory gate
  over 51 Provider calls but failed task quality; the financial-document probes
  reached 16--18 calls but ended with `finish_reason=length`
- Provider finding: raising `max_tokens` from 8,192 to 16,384 did not eliminate
  exact-limit terminations while LongCat thinking remained enabled
- scope: rejection-aware task selection evidence, not an FBGC quality or cost
  claim

The stock Terminal-Bench 2.0 verifier scripts inspected in this search all
perform some dynamic test-runner installation. Candidate stability was therefore
defined by deterministic assertions and the absence of stochastic sampling,
runtime-speed thresholds, or large system-package setup. The next controlled
variable should be Provider thinking mode, not another unbounded task search.
