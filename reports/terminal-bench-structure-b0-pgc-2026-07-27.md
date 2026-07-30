# Structure Harbor / Terminal-Bench Report — 2026-07-27

## Scope

- Harness: Harbor 0.20.0 with Docker
- Dataset: Terminal-Bench 2 at commit
  `69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`
- Provider: LongCat-2.0 through the OpenAI-compatible API
- Provider options: thinking enabled, `max_tokens=4096`, 240-second request
  timeout
- Strategies: B0 Full Replay and checkpoint PGC
- PGC checkpoint: eight eligible closed batches
- Agent limit: 128 model steps, subject to each task's official agent timeout
- Tasks: `custom-memory-heap-crash`, `db-wal-recovery`, `query-optimize`
- Scheduled attempts: three per task and strategy, 18 total

The API key was injected through 1Password Environment and was not written to
the repository or reports. The Harbor integration is an external-agent bridge:
the agent loop, Provider calls, event history, TTL, PointerGC, archive, and
metrics stay in Rust. A thin Python `BaseAgent` adapter only forwards `shell`
requests to Harbor's isolated `BaseEnvironment.exec` API.

## Validity classification

Harbor scheduled all 18 requested trials. Not all are valid model-policy
samples:

- B0: three valid C++ samples, two valid WAL samples, one valid Query sample;
  one WAL and two Query trials failed on the first Provider request with zero
  tokens.
- PGC: three valid C++ samples, three valid WAL samples, and two Query agent
  attempts. One Query task failed while pulling its Docker manifest and never
  ran the model. One Query attempt reached the 900-second agent timeout but had
  already produced a verifier-passing `sol.sql`; its incremental token report
  predates the partial-recorder fix and is unavailable.

Zero-token Provider failures and the Docker pull failure are preserved in the
Harbor artifacts but excluded from token summaries. Three additional valid
Query samples and one B0 WAL sample were planned. They are currently blocked by
repeated 1Password desktop authorization timeouts.

## Verifier outcome

| Strategy | C++ crash | WAL recovery | Query optimization |
|---|---:|---:|---:|
| B0 | 0/3 | 0/3 scheduled, 0/2 valid model | 0/3 scheduled, 0/1 valid model |
| PGC | 0/3 | 0/3 | 1/3 scheduled; pass occurred at agent timeout |

The single PGC Query pass proves that the Rust agent/Harbor bridge can produce a
real verifier-accepted task artifact. It does not establish a PGC success-rate
advantage: the passing attempt has an agent-timeout exception, two PGC Query
samples are not complete valid repetitions, and B0 has only one valid Query
sample.

## Per-sample model metrics

`Input` includes cached tokens. `Uncached` is `input - cached`. A tool error is
often a useful failed diagnostic command and is not itself a Harbor failure.

| Strategy | Task | Trial | Reward | Input | Uncached | Cached | Tools | Pointer appearances |
|---|---|---|---:|---:|---:|---:|---:|---:|
| B0 | C++ | `GsCTgSm` | 0 | 217,745 | 16,017 | 201,728 | 36 | 0 |
| B0 | C++ | `ZEtzYYY` | 0 | 936,454 | 35,078 | 901,376 | 62 | 0 |
| B0 | C++ | `ieT33MM` | 0 | 25,040 | 8,528 | 16,512 | 12 | 0 |
| PGC | C++ | `Qu49s7c` | 0 | 18,396 | 8,796 | 9,600 | 13 | 0 |
| PGC | C++ | `X59ch3w` | 0 | 25,056 | 9,696 | 15,360 | 12 | 0 |
| PGC | C++ | `qz7ax2Z` | 0 | 33,197 | 7,597 | 25,600 | 12 | 0 |
| B0 | WAL | `E76kP6f` | 0 | 56,618 | 7,594 | 49,024 | 17 | 0 |
| B0 | WAL | `gAycEZi` | 0 | 259,208 | 23,816 | 235,392 | 25 | 0 |
| PGC | WAL | `N9UtdoK` | 0 | 71,536 | 27,120 | 44,416 | 15 | 8 |
| PGC | WAL | `XeHinrJ` | 0 | 86,581 | 21,173 | 65,408 | 17 | 24 |
| PGC | WAL | `oQambQz` | 0 | 10,862 | 9,454 | 1,408 | 4 | 0 |
| B0 | Query | `w3ToaDJ` | 0 | 48,164 | 6,948 | 41,216 | 19 | 0 |
| PGC | Query | `yrjJR2y` | 0 | 30,110 | 4,638 | 25,472 | 14 | 0 |
| PGC | Query | `QpBZ82S` | 1 | unavailable | unavailable | unavailable | unavailable | unavailable |

The three C++ repetitions are complete for both strategies. Their median
inputs were 217,745 for B0 and 25,056 for PGC, while every run failed. PGC did
not reach a complete eight-batch checkpoint in those traces, so this difference
cannot be attributed to pointer replacement; it is dominated by stochastic
trajectory length. B0's 62-tool outlier also shows why total-token aggregates
without success and step controls are misleading.

WAL is the only task where PGC definitely activated. Two of three PGC samples
crossed checkpoints, with 8 and 24 cumulative pointer appearances. Neither
called `memory_search` or `memory_read`, and all WAL samples failed. This is
evidence that provider-silent archival works in a real task, but it raises a
retrieval-policy question: the model either did not need the archived evidence
or did not recognize when to retrieve it.

## Engineering findings

1. A real Terminal-Bench integration requires a shell-capable environment
   boundary. The previous `write_file`-only Tier-B runner was not sufficient.
2. Model-step count must be configurable. The Harbor profile uses 128 while the
   service default remains 32.
3. Provider generation controls matter. An unbounded initial smoke spent more
   than seven minutes in one LongCat response. The adapter now sends thinking,
   a token bound, and a request timeout.
4. Partial metrics are necessary. Harbor can cancel an agent while a Provider
   call is pending; provider-call observations are now flushed after every
   response.
5. External-agent cancellation must terminate its Rust child. The initial
   adapter left one timed-out child alive until manually terminated; the fixed
   adapter terminates and then kills after a five-second grace period.
6. Provider availability must be separated from policy quality. Zero-token
   network failures and Docker registry failures cannot be scored as memory
   strategy failures.

## Conclusion and next gate

This run validates the Harbor/Terminal-Bench execution path and produces the
first real task that both changed a container and passed an official verifier.
It does not yet support a paper claim that PGC improves successful-task token
efficiency. The current LongCat scaffold has too low a pass rate, the valid
sample matrix is incomplete, and the only passing sample ended at the timeout
boundary.

Before expanding the task set:

1. Re-authorize 1Password and fill the four missing valid samples.
2. Add a compact progress ledger that is pinned independently of raw tool
   evidence; PGC should collect completed subgoals without replaying full logs.
3. Make retrieval eligibility observable: count archive search opportunities,
   not only model-issued reads.
4. Repeat with a stronger terminal-agent model or a second provider. Memory
   efficiency should be compared conditional on verifier success and matched
   tool-step horizons.

## Local artifacts

- PGC job: `target/harbor-terminal-bench/structure-tbench-pgc-3x3`
- B0 job: `target/harbor-terminal-bench/structure-tbench-b0-3x3`
- Preliminary smoke: `target/harbor-smoke/structure-tbench-smoke-b0-v3`

The target artifacts are ignored by Git. This report preserves the reviewable
configuration, measurements, invalid-sample classification, and limitations.

## WAL completion follow-up — 2026-07-29

The WAL task was rerun sequentially with the same LongCat provider and the PGC
arm. The provider output ceiling was raised from 4,096 to 8,192 tokens and the
request timeout from 240 to 480 seconds. Tool output was capped at 8,000
characters.

The first follow-up (`cqyVEXx`) still failed. Although the prompt explicitly
required a backup, the model opened `main.db` with SQLite before copying the
sidecar. SQLite discarded the invalid encrypted WAL. The model then searched
for the missing file, dumped the base database, and exhausted all 8,192 output
tokens in its final reasoning response. Reward was 0; it used 55,314 input
tokens (23,186 uncached), 9,003 output tokens, 10 tool calls, and 11 Provider
calls.

This demonstrated that a prompt-only data-preservation rule was insufficient.
The Harbor shell boundary was therefore changed to make a one-time safety copy
of database inputs and sidecars at `/tmp/structure-input-snapshot` before the
first model-requested command. The location is disclosed in the system prompt,
so the model can restore or inspect the original evidence if a database client
removes it.

The second follow-up (`hWkdXR6`) passed the official verifier with reward 1.0:
all seven checks passed, including record completeness, exact recovered data,
ordering, uniqueness, and WAL decryption. The model identified the XOR key,
replaced the encrypted sidecar with a decoded WAL, queried all 11 records, and
wrote `/app/recovered.json`.

| Strategy | Trial | Reward | Input | Uncached | Cached | Output | Provider calls | Tools | Pointer appearances |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| PGC | `cqyVEXx` | 0 | 55,314 | 23,186 | 32,128 | 9,003 | 11 | 10 | 0 |
| PGC | `hWkdXR6` | 1 | 57,644 | 9,132 | 48,512 | 4,033 | 12 | 11 | 0 |

The successful run had an 84.2% aggregate cache-hit share and completed the
agent phase in 111.3 seconds. PGC did not replace history in this trajectory:
11 tool calls remained below the effective recency/checkpoint eligibility
window, so pointer appearances, `memory_search`, and `memory_read` were all
zero. This run proves the Structure/Harbor agent can complete the WAL task, but
it is not evidence of PGC token savings. A controlled B0/PGC comparison still
requires successful matched trajectories long enough to trigger PointerGC.

Follow-up artifacts:

- Failed prompt-only guard: `target/harbor-terminal-bench/structure-wal-pgc-pass-1`
- Passing enforced-snapshot run: `target/harbor-terminal-bench/structure-wal-pgc-pass-2`

## Frozen-scaffold matched WAL study — 2026-07-29

The passing scaffold was frozen before this study. No source, prompt,
generation, or tool-boundary changes were made between arms. The immutable
binary used by both jobs was
`target/harbor-scaffolds/wal-v1/harbor_agent`, SHA-256
`c1b9f569bae431e2751a4a36f5fb9e0a1424be70ec9bf8b5786935bae669b00b`.
Both arms used Terminal-Bench 2.0 `db-wal-recovery`, LongCat-2.0, 8,192 maximum
output tokens, 128 model steps, checkpoint batches 8, and concurrency 1. The
only changed agent parameter was `strategy`.

| Strategy | Trial | Reward | Input | Uncached | Cached | Output | Provider calls | Tools | Pointer appearances | Agent ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B0 | `CUw6LH9` | 1 | 90,976 | 9,568 | 81,408 | 10,180 | 16 | 15 | 0 | 252,077 |
| B0 | `baWtCyG` | 1 | 77,497 | 10,425 | 67,072 | 13,175 | 16 | 15 | 0 | 333,216 |
| B0 | `tNT583j` | 1 | 49,415 | 13,063 | 36,352 | 1,596 | 9 | 10 | 0 | 48,534 |
| PGC | `FnF3yhb` | 0 | 16,182 | 5,558 | 10,624 | 8,691 | 8 | 7 | 0 | 208,928 |
| PGC | `mcvi9WX` | 1 | 47,308 | 9,164 | 38,144 | 4,438 | 10 | 9 | 0 | 115,303 |
| PGC | `zJokc98` | 1 | 95,474 | 22,386 | 73,088 | 5,777 | 16 | 18 | 32 | 156,118 |

B0 passed 3/3; PGC passed 2/3. The PGC failure happened before any checkpoint
became eligible: its eighth Provider response consumed the full 8,192 output
tokens with `finish_reason=length`. It is therefore a model-generation failure,
not a PointerGC failure. All five successful trials passed all seven official
verifier tests.

Across successful samples, mean total input was 72,629 for B0 and 71,391 for
PGC. This 1.7% difference is not attributable to PGC: one successful PGC trial
was too short to activate it, and model trajectories varied from 9 to 16
Provider calls. Mean uncached input was worse for PGC (15,775 versus 11,019),
driven by the checkpoint cache break in the one active-PGC trajectory.

The useful within-trace observation is `zJokc98`. Pointer replacement first
appeared at Provider call 13. Model input bytes fell from 22,304 at call 12 to
21,168 at call 13 despite a new tool batch, a 5.1% immediate context-size
reduction. However, cached input simultaneously fell from 10,112 tokens to
zero, making call 13 consume 9,739 uncached tokens. Calls 14–16 recovered cache
reuse, but the one-time invalidation dominated the short remaining horizon.
The trial still passed without `memory_search` or `memory_read`, showing that
the archived evidence was no longer required for task correctness.

This matched study supports three narrower conclusions:

1. The frozen scaffold is now reliable enough to complete WAL: 5/6 total
   trials passed, with the only failure caused by a pre-GC model length cutoff.
2. PointerGC can reduce projected context while preserving correctness in a
   real successful task.
3. On this horizon, the checkpoint rewrite costs more uncached tokens than it
   saves. The next optimization should preserve the Provider-stable prefix and
   place pointer/GC material in an append-only suffix, or delay GC until the
   predicted future-token savings exceed the cache invalidation cost.

Frozen-study artifacts:

- Scaffold manifest: `target/harbor-scaffolds/wal-v1/manifest.txt`
- B0 job: `target/harbor-terminal-bench/structure-wal-frozen-b0-3x`
- PGC job: `target/harbor-terminal-bench/structure-wal-frozen-pgc-3x`
