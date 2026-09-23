# Runtime Short-Memory LongCat Report — 2026-07-27

## Scope

- Commit: `d51fa6d003fe74f544146c452b86b4d6ade5b0e6`
- Provider: LongCat 2.0 through an OpenAI-compatible Chat Completions endpoint
- Evidence level: `live_api`
- Secret injection: 1Password Environment through `op run --environment`
- Task: retain an exact value, traverse eight unrelated turns, recover the
  value, call the real `LocalRunner` `write_file` tool, and pass an exact file
  oracle
- Strategies: B0 Full Replay, B2 TTL-only, B3 Batch-only, and S production
  policy
- Repetitions: one per strategy

The API key was never serialized in the benchmark report. The 1Password
Environment values contained literal surrounding quotes; the successful runs
removed those quotes inside the child process without writing the values to
disk.

## Workload-model correction

The `--history-turns` workload does not represent one long agent task. Each
setup prompt is dispatched as a separate `Command::MessageSend`, so the
30-turn run below contains 32 accepted user messages: one evidence message,
30 distractor messages, and one evaluated request. It is valid as a
multi-message chat-history stress test, but it must not be used to infer the
behavior of a single user request.

A real single-request agent task has exactly one `MessageAccepted`. Its model
and tool steps remain inside the same run and are carried as provider
continuation items. At the time of the original runs below, Runtime projected
short memory once before that run started, so TTL and BatchKey policy did not
act on same-run tool events. The implementation now persists run events through
the canonical Session log and reprojects before every model step. The
benchmark exposes a separate single-message agent workload and records the
accepted user-message count as a correctness metric.

## Preliminary live smoke

The production policy completed one short two-turn task:

| Result | Provider calls | Input tokens | Output tokens | Cached input | Provider latency | Tool calls |
|---|---:|---:|---:|---:|---:|---:|
| Pass | 3 | 1,080 | 250 | 256 | 11,218 ms | 1 |

## Eight-turn comparison

| Strategy | Result | Calls | Input | Output | Cached | Provider latency | Final model-memory items | Final model-memory bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| B0 | 1/1 | 11 | 5,735 | 523 | 4,480 | 39,467 ms | 18 | 2,294 |
| B2 | 1/1 | 11 | 5,736 | 487 | 4,992 | 32,142 ms | 18 | 2,294 |
| B3 | 1/1 | 11 | 11,602 | 614 | 8,064 | 33,782 ms | 10 | 4,848 |
| S | 1/1 | 12 | 14,048 | 1,172 | 9,600 | 47,720 ms | 11 | 5,524 |

Every strategy recovered the exact evidence, executed `write_file`, produced
the expected file, and emitted `TASK_COMPLETE`. No evaluated-run tool call was
redundant.

## Findings

### Entry count is not a compression metric

B3 reduced the final item count from 18 to 10 but increased model-memory bytes
from 2,294 to 4,848. Its final decision call used 1,832 input tokens instead of
B0's 746. A short raw user/assistant pair was cheaper than the corresponding
metadata-heavy BatchKey. Compression must therefore be gated by serialized
model-input bytes and estimated or provider-reported tokens, not entry count.

### TTL was not exercised

B0 and B2 had identical model-memory shapes and almost identical input-token
totals. The default `recency_floor` of 100 protected all events in this trace,
so B2 behaved as Full Replay. The next policy revision lowers the default floor
to 20 and must be validated on a longer task.

### S contains one model-variance outlier

On S's first setup turn, where every policy had empty short memory, the model
unexpectedly called `write_file` to persist the evidence. The call failed
because its invented parent directory did not exist, and the model recovered.
This explains S's extra provider call and part of its extra token and latency
cost; it is model variance rather than a policy effect.

### Setup tool errors are under-reported

Tier-B token and latency aggregates include setup calls, while task tool/error
checks include only the evaluated run. The S setup error is present in protocol
events but `tool_error_count` remains zero. Future reports should expose setup,
evaluated-run, and total tool/error counts separately.

## Required changes

1. Use serialized model-input bytes and estimated tokens as the compression
   gate.
2. Keep a batch at `LOAD_ALL` unless its serialized BatchKey is smaller than
   its full provider-facing items.
3. Compute a per-batch key-content budget:

   `min(configured key-content limit, raw item bytes × target compression ratio)`

4. Lower the production `recency_floor` from 100 to 20.
5. Re-run a long fixture and a live LongCat task after the policy change.

## Artifacts

- Short live smoke: `target/longcat-real-smoke-normalized-d51fa6d/report.json`
- Eight-turn live comparison:
  `target/longcat-real-comparison-d51fa6d/report.json`

Target artifacts are local and ignored by Git; this report preserves the
reviewable measurements and limitations.

## Post-change validation

The following changes were then applied in the working tree:

- default `recency_floor`: 100 → 20;
- per-batch key-content budget: 384 bytes maximum and 60% of raw model-item
  bytes by default;
- strict benefit gate: keep `LOAD_ALL` unless the serialized BatchKey item is
  smaller than the corresponding full model items;
- benchmark hard gate: materialized model-input bytes must not exceed Full
  Replay;
- byte and estimated-token savings are first-class benchmark metrics.

### Offline validation

A 30-turn fixture comparison passed for B0, B2, B3, and S. Each strategy ended
with 62 model-memory items and 8,003 serialized bytes. The short turn batches
were correctly marked as having no key benefit and stayed full, preventing the
previous B3/S expansion.

A 100-turn synthetic trace with two tools per turn exercised profitable keys
and TTL:

| Full Replay bytes | Structure bytes | Bytes saved | Structure size |
|---:|---:|---:|---:|
| 138,945 | 22,296 | 116,649 | 16.04% |

The model-input non-expansion gate passed. Structure used 102 `LOAD_ALL`
batches, two profitable `LOAD_KEY` batches, 197 `NO_LOAD` batches, and retained
98 short batches at full fidelity because their keys would not save bytes.

### Active-run projection follow-up

The corrected single-message fixture sends one user message and executes six
`write_file` calls sequentially in the same run. It passed all six exact file
oracles with seven provider steps. The second provider step retained the newest
call/result as two lossless continuation items. From the third step onward,
older closed tool batches moved into active `run_memory`; the latest tool step
remained excluded from that projection and stayed lossless. Session assigned
the official event IDs and monotonically ordered sequences as each tool event
occurred, rather than after the run completed.

The original Tier-B v2 report records historical short-memory bytes, active-run memory
bytes, continuation item counts, tool calls, and exactly one accepted user
message. The local fixture artifact is
`target/tier-b-single-message-session-log/report.json`.

The same six-step workload then passed against the live LongCat provider:

| Result | User messages | Tool calls | Provider calls | Input | Output | Cached | Provider latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| Pass | 1 | 6 | 7 | 7,241 | 392 | 3,840 | 97,628 ms |

All six files matched exactly, with no provider, tool, or redundant-call
errors. Calls one and two had no projected active-run memory. Calls three
through seven carried one through five older closed batches respectively,
ending at 2,993 serialized bytes. Every call after the first retained exactly
two lossless continuation items for the newest tool call/result. Canonical
Session event sequences were contiguous from 1 through 23. The first provider
call contributed 78,726 ms of the total latency; subsequent calls ranged from
2,245 to 4,588 ms.

The local live artifact is
`target/longcat-single-message-step-projection/report.json`.

### Active-run B0/B2/B3/S comparison

Tier-B v3 added an explicit `batch_compaction_enabled` switch so B0 and B2
cannot accidentally emit BatchKeys for older same-run tool batches. It also
records uncached input tokens, continuation bytes, and the complete
provider-neutral request bytes for every model call. The active-run benefit
gate now excludes `CommandOutput` observations that Runtime deliberately does
not send to the Provider; counting those duplicate events previously made an
unprofitable key appear smaller than the actual full projection.

The corrected one-user-message, six-tool LongCat comparison completed 28 real
model calls:

| Strategy | Result | Calls | Input | Uncached input | Cached | Output | Request bytes | Provider latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| B0 | Pass | 7 | 6,170 | 2,330 | 3,840 | 500 | 23,951 | 19,778 ms |
| B2 | Pass | 7 | 6,190 | 2,094 | 4,096 | 536 | 24,134 | 32,044 ms |
| B3 | Pass | 7 | 7,069 | 3,101 | 3,968 | 473 | 23,428 | 22,453 ms |
| S | Pass | 7 | 7,083 | 2,731 | 4,352 | 756 | 23,411 | 29,393 ms |

Every strategy produced all six exact files, used one accepted user message,
executed six non-redundant tool calls without errors, and emitted contiguous
Session event sequences 1 through 23. Every call after the first retained the
latest tool tail as lossless continuation. The B2 second response included an
extra assistant item alongside its tool call, which accounts for its 183-byte
request difference from B0 and is model-output variance rather than a TTL
effect.

This comparison does **not** prove token savings. Relative to B0, S reduced
provider-neutral request bytes by 540 bytes (2.25%) but increased reported
input tokens by 913 (14.80%) and uncached input tokens by 401 (17.21%). B3
showed the same pattern: 2.18% fewer request bytes but 14.57% more input tokens.
The divergence is systematic after BatchKeys appear: B0 versus S input tokens
on calls three through seven were `753/814`, `881/1006`, `1011/1191`,
`1140/1380`, and `1268/1575`.

The cause is the Provider compilation boundary. Native full batches remain an
assistant tool call plus a tool result. A BatchKey is converted into a system
message with a fixed safety preamble and XML delimiters before OpenAI Chat
encoding. The provider-neutral key item can therefore be a few bytes smaller
while its compiled text consumes substantially more tokens. The next gate must
compare provider-compiled wire bytes or provider-specific token estimates; the
current item-byte gate is a non-expansion safeguard, not a token-savings proof.

The local live comparison artifact is
`target/longcat-single-message-comparison-v3/report.json`.

### Recency-floor long-task comparison

A six-tool run has too few events to distinguish B0 from B2 under
`recency_floor=20`. A twelve-tool fixture was therefore added. The Runtime's
previous eight-step loop limit terminated the first attempt after eight tools;
the bounded limit was raised to 32 model steps and a twelve-tool conformance
test now covers the longer single-message path.

The corrected fixture completed all four strategies with one user message,
twelve tools, thirteen provider steps, exact file oracles, and no redundant or
failed calls:

| Strategy | Result | Request bytes | Bytes saved vs B0 | Savings |
|---|---:|---:|---:|---:|
| B0 | Pass | 76,040 | 0 | 0.00% |
| B2 | Pass | 66,137 | 9,903 | 13.02% |
| B3 | Pass | 73,501 | 2,539 | 3.34% |
| S | Pass | 64,127 | 11,913 | 15.67% |

This proves that the 20-event recency floor is crossed and that TTL and
BatchKey produce distinct Runtime projections. It is structural fixture
evidence, not provider token evidence. The local artifact is
`target/tier-b-single-message-long12-32step-v3/report.json`.

After the 1Password beta CLI was re-authorized, the matching LongCat comparison
ran to completion. The planned 52 calls became 64 because S repeated the
entire tool sequence:

| Strategy | Result | Calls | Tools | Redundant | Input | Uncached | Cached | Request bytes | Provider latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B0 | Pass | 13 | 12 | 0 | 19,173 | 3,685 | 15,488 | 78,758 | 55,394 ms |
| B2 | Pass | 13 | 12 | 0 | 17,243 | 8,795 | 8,448 | 68,330 | 53,787 ms |
| B3 | Pass | 13 | 12 | 0 | 23,438 | 5,902 | 17,536 | 76,285 | 91,346 ms |
| S | **Fail** | 25 | 24 | 12 | 44,242 | 32,594 | 11,648 | 144,632 | 102,636 ms |

B2 is the only valid strategy that reduced total provider-reported input
tokens: 10.07% below B0, alongside 13.24% fewer provider-neutral request
bytes. Its uncached input tokens nevertheless increased by 138.67% because
LongCat reported much less prompt caching, so total-token reduction does not
directly establish billing reduction. B3 passed but used 22.24% more input
tokens despite 3.14% fewer request bytes, confirming the codec/token expansion
already seen in the six-tool run.

S wrote every expected file correctly and returned `TASK_COMPLETE`, but the
oracle rejected it because it executed paths 0001 through 0012 twice in the
same order. There were no Provider failures or tool errors. From call eight
onward, S's active `run_memory` plateaued at six BatchKeys (about 3,319 bytes),
while older completed batches became `NO_LOAD`. After the first twelve tools,
the model no longer had recoverable completion evidence for the earlier half
of the task and restarted at file 0001. This is consistent with a policy
correctness failure at the TTL-plus-BatchKey boundary, although one live
repetition cannot by itself quantify failure probability.

B2 also retained only six older completed batches after the floor was crossed,
but kept them as twelve native tool call/result items and completed without a
retry. B3 retained all older batches as eleven keys and also completed. The
combination in S therefore exposes a failure neither ablation produced alone:
TTL removes the older progress ledger while the remaining BatchKeys are less
explicit than native tool pairs.

The local live artifact is
`target/longcat-single-message-long12-comparison-v3/report.json`.

## PointerGC v4: persistent paths and live LongCat comparison

Tier-B v4 adds an independent `PGC` strategy. It applies TTL and relation-based
Event GC without BatchKey, archives exact closed batches through the file-backed
`LongMemoryStore`, and exposes compact logical paths such as
`m/tool/write_file/<sha256>.json`. The same path is usable as a SQLite primary
key. The model never receives an absolute host path.

The deterministic 12-tool, one-user-message fixture passed all five strategies.
PGC activated 15 pointer appearances backed by five unique content-addressed
files and reduced provider-neutral request bytes from 79,953 (B0) to 76,696
(-4.07%). Repeated appearances reference the same immutable files rather than
rewriting evidence.

The live LongCat-2.0 comparison was run through 1Password Environment injection
with no API key serialized in the report:

| Strategy | Pass | Provider calls | Tool calls | Input tokens | Uncached | Cached | Request bytes | BatchKeys | Pointers |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B0 | Yes | 13 | 12 | 20,088 | 3,192 | 16,896 | 82,125 | 0 | 0 |
| B2 | No | 19 | 18 | 28,106 | 18,506 | 9,600 | 114,000 | 0 | 0 |
| B3 | No | 32 | 32 | 115,860 | 12,180 | 103,680 | 393,688 | 465 | 0 |
| S | No | 23 | 22 | 41,568 | 26,848 | 14,720 | 138,901 | 111 | 0 |
| PGC | Yes | 13 | 12 | 20,384 | 7,200 | 13,184 | 78,673 | 0 | 15 |

PGC and B0 were the only strategies that completed all twelve exact writes
within the tool-call budget. B2 repeated calls and omitted the twelfth file; B3
reached Runtime's 32-step bound without a terminal answer; S wrote every file
but repeated calls and exceeded the tool budget.

PGC reduced provider-neutral bytes by 3,452 versus B0 (-4.20%), but increased
provider-reported input tokens by 296 (+1.47%) and uncached input tokens by
4,008. Therefore this run proves correctness plus byte compression, **not token
savings or improved cache reuse**. Provider tokenization and cache boundaries
remain distinct from provider-neutral JSON size. Artifact:
`target/longcat-pointer-gc-v4/report.json`.

### Live LongCat 30-turn chat-history stress

The revised production policy passed one live multi-message session with 30
unrelated history turns:

| Result | Calls | Input | Output | Cached | Provider latency | Final items | Final bytes |
|---|---:|---:|---:|---:|---:|---:|---:|
| Pass | 33 | 34,969 | 1,450 | 31,616 | 102,052 ms | 62 | 7,793 |

The evaluated turn recovered the exact evidence, called `write_file` once,
had no tool or provider errors, and completed with `TASK_COMPLETE`. The first
evaluated call used 1,845 input tokens; the tool-result continuation used
1,956, of which 1,792 were reported as cached.

The memory shape grew linearly from 18 items / 2,294 bytes at call 10 to 38 /
4,793 at call 20, 58 / 7,293 at call 30, and 62 / 7,793 at completion. This is
expected for the constructed chat-history workload because every accepted user
message is currently classified as a pinned Anchor. It does not show that a
single-message task accumulates user messages. Short-batch benefit gating
prevents expansion in long chats. The follow-up active-run implementation now
measures older closed batches through `run_memory` while keeping only the
latest tool step in lossless continuation.

The live artifact is local at
`target/longcat-long-s-benefit-floor20/report.json`.

## Checkpoint PointerGC follow-up

The first PointerGC implementation rewrote old tool pairs into pointer text on
every eligible turn. Although it reduced provider-neutral bytes, it repeatedly
changed the provider prefix and destroyed cache reuse. The revised design uses
complete checkpoint epochs, keeps the open epoch lossless, omits internal
pointers from provider messages, and exposes archives through fixed
`memory_search` and `memory_read` tools. The benchmark interval is four
eligible batches; the production default is eight.

A focused LongCat A/B used one user message and twelve requested file writes:

| Strategy | Result | Calls | Tools | Input | Uncached | Cached | Request bytes | Pointer appearances |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| B0 | Fail tool budget | 14 | 13 | 24,100 | 4,388 | 19,712 | 100,697 | 0 |
| PGC | Pass | 13 | 12 | 20,568 | 6,104 | 14,464 | 87,307 | 8 |

B0 produced all expected files but issued a thirteenth tool call, so aggregate
token differences are partly confounded by model behavior. The checkpoint
transition itself is still directly observable. At call 12, PGC replaced four
closed batches and reported a cache miss; its input fell from B0's 2,288 tokens
to 1,788. At call 13 the new prefix was reused immediately: PGC reported 1,664
cached and 251 uncached tokens, while total input remained 498 tokens below B0.
This validates one-time checkpoint invalidation followed by cache recovery. It
does not yet prove lower uncached-token cost: a longer post-checkpoint horizon
is needed to amortize each transition.

Artifact: `target/longcat-checkpoint-pgc-v4/report.json`.

## Terminal-Bench 2 candidate set

Terminal-Bench is the appropriate next layer because its tasks have real Linux
environments, deterministic verifiers, large tool outputs, and enough elapsed
steps to cross multiple PointerGC checkpoints. Start with this official subset:

| Task | Why it stresses memory | Expert estimate |
|---|---|---:|
| `custom-memory-heap-crash` | iterative release/debug builds, debugger and Valgrind evidence, recovery from failed hypotheses | 30 min |
| `db-wal-recovery` | binary inspection, SQLite/WAL experiments, exact recovered-data oracle | 45 min |
| `query-optimize` | repeated query-plan inspection and performance comparison while preserving exact output | 60 min |
| `llm-inference-batching-scheduler` | multi-file analysis, numerical optimization, repeated cost-model feedback | 45 min |
| `git-multibranch` | SSH, Git hooks, Nginx, TLS, services, and end-to-end deployment verification | 180 min |

Use `log-summary-date-ranges` as a short control, not as the main long-context
claim. Defer `large-scale-text-editing`: its score primarily measures a narrow
Vim-macro construction constraint, so it is less diagnostic of memory policy.
For every selected task, compare B0 and PGC with the same model, task image,
attempt count, and provider cache state. Record verifier reward, steps,
provider input/cached/uncached tokens, checkpoint count, archive reads, repeated
tool calls, latency, and cost. At least three attempts per task are needed to
separate policy effects from agent variance.
