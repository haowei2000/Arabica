# Build-Cython-Ext Interleaved B0/PGC Study — 2026-07-30

## Result

The frozen `B0, PGC, B0, PGC, B0, PGC` LongCat experiment completed all six
attempts on Terminal-Bench `build-cython-ext`. The result is a clear negative
for the current PointerGC policy.

PointerGC reduced total Provider input and peak context size, but increased the
primary economic metric, uncached input tokens, in every paired attempt. All
three B0 agents reached a normal terminal response; all three PGC agents
exhausted the 128-call budget while still requesting tools. B0 produced the
only complete verifier success (11/11, reward 1). PGC never called
`memory_search` or `memory_read`. Post-experiment code and raw-request audit
found that the reported pointer appearances were internal projection counts:
the Provider encoder discarded `MemoryPointer` items, so their paths and
retrieval hints never reached the model. Each attempt still archived 108–120
tool records.

The present policy must not be described as token-cost saving. It is a
context-size reduction mechanism whose cache resets and degraded task
convergence more than erased its benefit in this experiment.

## Frozen configuration

- Model/provider: LongCat OpenAI-compatible API, `LongCat-2.0`
- Task: `build-cython-ext`
- Task image: `ghcr.io/laude-institute/terminal-bench/build-cython-ext:2.0`
- Order: `B0, PGC, B0, PGC, B0, PGC`
- Attempts/concurrency: one per job / one
- Agent and verifier timeouts: 900 seconds each
- Maximum Provider calls: 128
- Maximum output tokens per call: 8,192
- Pointer checkpoint interval: 4 batches
- `PGC_EFFORT`: 1
- Continuation probability: 7,500 basis points
- Archive adapter: local files
- Frozen binary:
  `target/harbor-scaffolds/build-cython-ext-ab-v1/harbor_agent`
- Binary SHA-256:
  `819a324401917a016b5955c2923c1acf079ed478bbdfaaa48a4b716038ba718a`

The binary hash still matched the frozen manifest after all attempts. The first
B0 attempt used the registry-resolved task. Later attempts used Harbor's local
task cache after a registry network failure. The cached `task.toml` and
`instruction.md` hashes exactly matched the frozen manifest:

- `task.toml`:
  `f61b6197005e8083ba1ee310678498c847ddf5a5b2733aab1d46e13d9663edba`
- `instruction.md`:
  `be4a4feef9bf9bd8e5efc93ccfc8f9bdeb91987dcc1fd98106625d8bc0833fa2`

The local task definition retained the same image and 900-second timeouts, so
the registry bypass did not change the task payload or verifier.

## Attempt results

| Order | Strategy | Provider calls | Tool calls | Input | Cached | Uncached | Output | Agent time | Terminal | Verifier |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 01 | B0 | 50 | 53 | 1,030,638 | 987,520 | 43,118 | 6,134 | 7.09 min | success | 10/11 |
| 02 | PGC | 128 | 128 | 754,253 | 652,160 | 102,093 | 12,059 | 12.72 min | step limit | infrastructure failure |
| 03 | B0 | 114 | 117 | 2,757,372 | 2,701,312 | 56,060 | 10,384 | 13.52 min | success | 10/11 |
| 04 | PGC | 128 | 133 | 743,007 | 638,720 | 104,287 | 13,606 | 13.28 min | step limit | 10/11 |
| 05 | B0 | 56 | 59 | 1,078,865 | 1,039,616 | 39,249 | 7,371 | 8.58 min | success | **11/11, reward 1** |
| 06 | PGC | 128 | 133 | 773,659 | 651,264 | 122,395 | 13,532 | 14.02 min | step limit | 9/11 |

Attempt 02's verifier did not execute the task tests: its setup could not
resolve `releases.astral.sh`, so `uv` was not installed. Its reward 0 is not a
quality observation. The agent-side step-limit, token, cache, raw-capture, and
PointerGC measurements remain valid. Container DNS was checked before later
attempts, whose verifiers executed normally.

## Aggregate strategy comparison

| Metric | B0 mean | PGC mean | PGC change |
|---|---:|---:|---:|
| Normal terminal rate | 3/3 | 0/3 | worse |
| Provider calls | 73.3 | 128.0 | +74.5% |
| Total input tokens | 1,622,292 | 756,973 | -53.3% |
| Cached input tokens | 1,576,149 | 647,381 | -58.9% |
| **Uncached input tokens** | **46,142** | **109,592** | **+137.5%** |
| Aggregate cache-hit rate | 97.2% | 85.5% | -11.6 pp |
| Output tokens | 7,963 | 13,066 | +64.1% |
| Provider latency | 404.0 s | 592.0 s | +46.5% |
| Agent elapsed time | 9.73 min | 13.34 min | +37.1% |
| Peak input tokens | 35,431 | 12,844 | -63.7% |
| Peak model-input bytes | 145,090 | 92,378 | -36.3% |
| Tool errors | 4.7 | 11.3 | +142.9% |

Among valid verifiers, B0 averaged 10.33/11 checks and succeeded once in three
attempts. PGC averaged 9.5/11 checks across its two valid verifiers and did not
succeed. This is descriptive at `n=3`, but it is already sufficient to reject
quality non-inferiority for the current configuration.

## Paired economic result

Every alternating pair has the same qualitative result: PGC transmitted less
total context but paid more uncached input.

| Pair | B0 uncached | PGC uncached | Change | B0 input | PGC input | Change |
|---|---:|---:|---:|---:|---:|---:|
| 01 → 02 | 43,118 | 102,093 | +136.8% | 1,030,638 | 754,253 | -26.8% |
| 03 → 04 | 56,060 | 104,287 | +86.0% | 2,757,372 | 743,007 | -73.1% |
| 05 → 06 | 39,249 | 122,395 | +211.8% | 1,078,865 | 773,659 | -28.3% |

The same-call-count prefix demonstrates why total input alone is misleading.
At call 16 in the first pair, PGC had reduced cumulative input from 153,990 to
85,903 and uncached input from 21,126 to 16,271. Over the full trajectory,
however, repeated cache resets and 72 additional Provider calls reversed the
uncached-token benefit.

## PointerGC behavior

| Attempt | Admissions | Cache resets | Pointer transition resets | Archive files | Memory search/read |
|---|---:|---:|---:|---:|---:|
| 02 PGC | 11 | 9 | 9 | 112 | 0 / 0 |
| 04 PGC | 12 | 7 | 7 | 120 | 0 / 0 |
| 06 PGC | 13 | 8 | 6 | 108 | 0 / 0 |

The first PGC attempt illustrates the intended local decision and the global
failure. At model step 16, the gate estimated 6,141 saved tokens per future
call against an 8,832-token cache reset, with 113 steps remaining. It admitted
GC, reduced the next projected request from 40,922 to 19,664 bytes, and created
eight internal file pointers. The Provider adapter then omitted those pointer
items from the wire request. That local size decision was plausible, but it
removed exact evidence without supplying a visible recovery address. The
policy then made another ten admissions, while the agent continued for the
full 128 calls and never retrieved an archive.

The defect is therefore not that GC fails to reduce an individual request. It
is that the gate treats each reset too independently and the runtime delegates
all recovery to optional model tool use. The result is repeated cache debt,
loss of directly visible progress evidence, more tool errors, and longer task
trajectories.

## P0 diagnosis

1. **The Provider-visible recovery address is missing.** Every PGC request
   exposed `memory_search` and `memory_read`, but `MemoryPointer` serialization
   returned `None`. The model therefore received neither the archive path nor
   its retrieval hint, and all three agents made zero recovery calls. Exact
   bytes on disk do not provide runtime recoverability when the active view
   omits their address.
2. **The admission gate lacks cumulative reset debt.** It estimates whether one
   GC can amortize one observed cache reset over a probability-weighted horizon.
   It does not reserve a minimum stable-reuse interval or account for the next
   GC invalidating the newly rebuilt prefix.
3. **Critical progress evidence is being treated like ordinary closed tool
   history.** Test failures, applied edits, task checklists, and the latest
   verification state must remain pinned or be automatically summarized into a
   stable working-state record. Archiving all old closed batches leaves the
   model able to rediscover the same facts only through more shell calls.
4. **Task progress is missing from the economic model.** The current gate can
   reduce bytes while the expected number of remaining calls grows. Uncached
   cost must include a trajectory-risk term, not only per-call savings.

## Required next change

Treat the current result as a P0 and disable the present aggressive PGC policy
as a default. The next implementation should combine:

1. Provider-visible pointer suffix items containing the exact archive path,
   relation metadata, integrity hint, and an explicit `memory_read` instruction;
2. a reset-debt ledger and cooldown: no new GC until the previous reset has
   been repaid by observed uncached-token savings over a minimum reuse window;
3. pinned working state for the latest failing tests, edits, decisions, and
   unresolved task checklist;
4. automatic targeted hydration when a pointer covers evidence relevant to the
   current tool/test loop, rather than depending solely on model-initiated
   `memory_read`;
5. loop-sensitive recovery: repeated commands, repeated failing tests, or rising
   tool-error rate should hydrate the most relevant archived batches before the
   next Provider call;
6. a trajectory-aware gate that charges an expected-extra-call penalty against
   projected GC savings.

After implementing these changes, freeze a new binary and repeat this exact
six-attempt order. Acceptance requires at least B0-non-inferior terminal and
verifier outcomes, lower paired uncached tokens, and evidence that any archive
needed later was actually read into the model loop.

## P0 implementation started — 2026-07-30

The first corrective slice is implemented, but has not yet been validated by
a new real-Provider A/B run:

1. `MemoryPointer` now becomes a Provider-visible system suffix after current
   run memory and before the active typed continuation. It includes the exact
   local archive path and an explicit `memory_read` argument.
2. Explicit model-step protection now overrides expired TTL. The latest two
   failed tool call/result pairs are pinned as working state even after their
   ordinary TTL would expire.
3. PointerGC now maintains reset debt, requires a minimum eight-call reuse
   window before another epoch, and increases effective `PGC_EFFORT` after
   each prior admission.
4. An exact repeated tool call can trigger loop-aware hydration of its newest
   matching archived batch. Runtime performs a real archive-store read,
   verifies the content hash, limits hydration to 64 KiB, and injects the
   archived canonical event JSON as non-instruction evidence for the next
   Provider call.
5. Harbor report schema `structure.harbor-agent/v5` records hydration count,
   hydrated bytes, trigger call ID, archive path, and model step.

This slice does not yet pin applied edits/checklists, infer semantic relevance
beyond exact tool-name/argument repetition, or charge expected extra calls in
the admission equation. It also cannot be called an economic improvement
until a frozen-binary B0/PGC comparison passes the quality and uncached-token
gates.

## Raw evidence and security

All attempts retained exact Provider request/response bytes and shell
request/stdout/stderr bytes. Provider raw directory counts matched Provider call
counts: 50, 128, 114, 128, 56, and 128. All expected response bodies were
present. Tool raw directories were also retained: 54, 129, 118, 134, 60, and
134.

A credential scan over every agent artifact found zero Authorization headers,
Bearer values, or LongCat-style key patterns. Raw artifacts remain under
`target/` and must not be committed.

## Artifacts

- `target/harbor-scaffolds/build-cython-ext-ab-v1/manifest.txt`
- `target/harbor-terminal-bench/structure-cython-ab-v1-01-b0-r3`
- `target/harbor-terminal-bench/structure-cython-ab-v1-02-pgc`
- `target/harbor-terminal-bench/structure-cython-ab-v1-03-b0`
- `target/harbor-terminal-bench/structure-cython-ab-v1-04-pgc`
- `target/harbor-terminal-bench/structure-cython-ab-v1-05-b0`
- `target/harbor-terminal-bench/structure-cython-ab-v1-06-pgc`

Credentials and raw task data are intentionally excluded from this report.
