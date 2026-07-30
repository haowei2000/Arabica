# Build-Cython-Ext FileBackedGC Smoke Test — 2026-07-30

## Result

The first real-Provider FileBackedGC (`FBGC`) smoke test completed a normal
agent terminal response after 98 Provider calls. File-backed compaction itself
worked: two admitted GC epochs replaced 12 closed tool batches with
Provider-visible file pointers, all 12 exact archives remained readable and
hash-valid, and reset debt prevented further unprofitable admissions.

The run does **not** establish quality or token savings. The agent never called
`memory_read` or `memory_search`, never triggered automatic hydration, and
entered a long validation loop. From tool call 63 onward it alternated an
already-successful README example with an already-passing 18-test suite. The
example ran 23 times in total and the test command 18 times. The verifier then
failed before executing task assertions because its independent `uv` bootstrap
could not resolve `astral.sh`; Harbor therefore recorded reward 0.0, but that
reward is an infrastructure failure rather than a valid task-quality score.

## Frozen configuration

- Branch: `codex/file-backed-gc`
- Git commit: `f8aa0f0262cb6fa4e289c2f16e9a257ec6ee268c`
- Strategy: `FBGC` / `file_backed_gc`
- Model/provider: LongCat OpenAI-compatible API, `LongCat-2.0`
- Task: Terminal-Bench `build-cython-ext`
- Maximum Provider calls: 128
- Maximum output tokens per call: 8,192
- Checkpoint interval: 4 closed batches
- Effort: 1
- Continuation probability: 7,500 basis points
- Frozen binary:
  `target/harbor-scaffolds/build-cython-ext-fbgc-v1/harbor_agent`
- Binary SHA-256:
  `0b96210e948860471373eef378abe4d676fbb06512018edcdd2e3c2ea61901ad`

## Runtime metrics

| Metric | FBGC smoke |
|---|---:|
| Provider calls | 98 |
| Tool calls | 97 |
| Tool errors | 8 |
| Total input tokens | 1,688,536 |
| Cached input tokens | 1,294,464 |
| **Uncached input tokens** | **394,072** |
| Cache-hit rate | 76.7% |
| Output tokens | 10,279 |
| Peak input tokens | 33,556 |
| Peak model-input bytes | 137,468 |
| Provider latency | 509.2 s |
| Agent elapsed time | 717.8 s |
| Harbor wall time | 12 min 21 s |
| Normal agent terminal | yes |
| Verifier | infrastructure failure |

For context only, the prior three frozen B0 runs averaged 73.3 Provider calls
and 46,142 uncached input tokens. This FBGC smoke used 394,072 uncached tokens,
8.5 times that B0 mean. This is not a paired comparison, so it cannot estimate
the causal FBGC effect, but it is already an economic failure for this sample.
The next experiment must alternate FBGC with a B0 run using the same new binary
and a functioning verifier.

## FBGC behavior

| Admission step | New archives | Removable bytes/call | Estimated saved tokens/call | Estimated reset tokens | Effective effort |
|---:|---:|---:|---:|---:|---:|
| 13 | 4 | 14,175 | 3,871 | 11,008 | 1 |
| 21 | 8 | 21,940 | 6,178 | 9,728 | 2 |

There were 90 admission checks and only two admissions. After the second
epoch, cumulative reset debt and the increasing effective effort rejected all
later candidates. The two pointer transitions produced two measured cache
resets. Across the complete run, pointers appeared 968 times in projected
Provider context.

The archive audit found 12 JSON files containing 83,762 bytes of canonical
event content. Every file name, `memory_id`, content hash, and recomputed
SHA-256 digest agreed. Thus TTL-zero hiding and exact persistence worked as
designed.

## Loop and recovery diagnosis

The long trajectory was dominated by an explicit tool loop rather than a
Provider or shell failure:

- README validation command: 23 exact executions; after call 63 it ran on
  every odd tool call through 97.
- Passing pytest command: 18 exact executions; it ran on every even tool call
  from 64 through 98.
- Each repeated README execution returned
  `Success! Result: 6.999999999999998`.
- Each repeated pytest execution reported `18 passed`.

The loop began 42 model steps after the second GC admission, so temporal
correlation alone does not show that an archived result caused it. More
importantly, the repeated commands were not among the early archived batches:
the runtime's exact-repeat hydration trigger therefore had no matching archive
to load. `memory_read_calls`, `memory_search_calls`, `auto_hydration_count`, and
`auto_hydrated_bytes` all remained zero. This sample proves pointer visibility
and durable storage, but it does not test successful archive recovery.

## Verifier limitation

The agent reached a normal `stop` and claimed successful installation and 18
passing repository tests. Harbor's verifier did not reach its task tests. Its
setup attempted to download `uv` from `astral.sh`, DNS resolution failed, and
the remaining commands failed because `uv` was absent. The zero reward must be
classified as invalid quality evidence. Agent-side token, cache, event,
compaction, tool, and raw-capture measurements remain usable.

## Raw evidence and security

- 98/98 Provider calls have `request.raw.json`, `response.raw`, and parsed
  response files.
- 97 shell calls plus the input-snapshot operation have request, result,
  stdout, and stderr artifacts.
- All original task input/output evidence remains under the ignored `target/`
  tree.
- A credential scan found no Authorization headers, Bearer values, or
  LongCat-style API keys in the trial artifacts.

## Next experiment

1. Add a loop detector for repeated successful tool commands. Repeating the
   same successful validation should pin or summarize the latest result and
   force a progress/termination decision, not execute it indefinitely.
2. Extend recovery matching beyond exact repeats of archived calls. Use
   relation metadata and current command intent to hydrate relevant archived
   evidence before rediscovery.
3. Run a small purpose-built recovery task where a needed result is archived
   in the first half and must be used in the second half; require a real
   `memory_read` or auto-hydration observation.
4. Repair or preflight the verifier network path, then freeze one binary and
   run interleaved `B0, FBGC, B0, FBGC, B0, FBGC` attempts. Acceptance requires
   valid verifier quality, no loop regression, and lower paired uncached-token
   cost.

## Artifacts

- `target/harbor-scaffolds/build-cython-ext-fbgc-v1/manifest.txt`
- `target/harbor-terminal-bench/structure-cython-fbgc-v1-01`

Credentials and raw task data are intentionally excluded from this report.
