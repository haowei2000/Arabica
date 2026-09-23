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

## Post-run archive audit and v2 optimization

The two admitted epochs were audited batch by batch before changing the
policy. Exact persistence was correct, but chronological selection and opaque
pointer metadata mixed low-risk bulk inspection with important working state.

### Epoch 1 — model step 13

| Sequence | Archived interaction | Archive bytes | Assessment |
|---:|---|---:|---|
| 5–7 | clone pyknotid 0.5.3 | 2,356 | safe after retaining a compact cloned-revision fact |
| 8–10 | inspect Python 3.13.7, NumPy 2.3.0, package absent | 2,381 | key environment state; poor opaque-pointer candidate |
| 11–13 | read `setup.py` | 12,314 | excellent bulk-read candidate |
| 14–16 | read `chelpers.pyx` | 15,055 | excellent bulk-read candidate |

The epoch persisted 32,106 bytes, estimated 14,175 removable active-context
bytes, and introduced a 2,979-byte Provider pointer suffix. The bulk source
reads were rational choices. The environment probe was not: its small result
contained high-value task state and the pointer exposed only a hash and call
ID.

### Epoch 2 — model step 21

| Sequence | Archived interaction | Archive bytes | Assessment |
|---:|---|---:|---|
| 17–19 | read `ccomplexity.pyx` | 14,078 | excellent bulk-read candidate |
| 20–22 | read `cinvariants.pyx` | 5,452 | good bulk-read candidate |
| 23–25 | read `coctree.pyx` | 16,976 | excellent bulk-read candidate |
| 26–28 | install Cython | 2,101 | compact state fact should survive |
| 29–31 | build failed: setuptools missing | 1,464 | recovery fact; should rank below bulk reads |
| 32–34 | install setuptools | 2,095 | compact state fact should survive |
| 35–37 | build extensions succeeded | 8,267 | critical progress milestone |
| 38–40 | editable install timed out | 1,215 | failure was later superseded, but relation was absent |

This epoch persisted another 51,656 bytes and estimated 21,940 removable
bytes. After it, all 12 pointer records occupied 8,939 Provider bytes. The
local size reduction was real, but `missing setuptools -> installed
setuptools -> build succeeded` became three unrelated opaque addresses. With
no operation or subject in the path/hint, the model had no basis for selecting
one for `memory_read`.

### Implemented v2 changes

An initial implementation parsed shell command strings inside Runtime/FBGC.
That approach was rejected because runner-specific command rules would
contaminate the generic GC policy. The corrected design separates typed
classification from GC admission:

`raw tool call -> runner classification -> tool.call.classified event -> TTL policy -> generic FBGC gate`

The Harbor shell adapter classifies interactions as `inspection`, `mutation`,
`build`, `dependency`, `validation`, or `generic`. Other runners can classify
their own tool vocabulary without teaching Runtime about shell commands. The
default TTL limits are respectively 1, 8, 8, 8, 6, and 3 decay units. These
values are initial experimental settings, not yet quality- or cost-optimal.

1. FileBackedGC ranks eligible new batches by net removable bytes before the
   checkpoint boundary. TTL expiry, relation closure, pinning, cache-reset
   debt, cooldown, and probability-weighted reuse remain the other admission
   signals. Existing PointerGC retains chronological behavior for comparison.
2. The classification event joins the corresponding closed tool batch but is
   not projected as an extra Provider message. Its independent TTL therefore
   acts as the retention floor for that interaction.
3. Archive paths use only typed protocol metadata: batch kind, tool name,
   sequence range, terminal status, and a hash suffix. For example:
   `m/tool/shell/000005-000007-success-<hash>.json`.
4. Pointer residue exposes only protocol-level facts: tool name, success/error
   status, sequence range, call ID, event count, and exact result size. It does
   not parse shell command strings or use model-generated compression; full
   canonical events remain in the file.
5. The Provider emits the recovery explanation once and keeps each subsequent
   append-only pointer record compact. It no longer repeats the full recovery
   paragraph for every archived batch.
6. Focused tests prove that a checkpoint chooses the four candidates with the
   largest generic net savings and keeps the smaller candidate resident, and
   that paths/hints do not copy task-specific command content. Separate tests
   cover runner classification and the TTL assigned to each typed interaction.

All workspace tests and Clippy with warnings denied pass. A deterministic
12-tool, one-user-message B0/FBGC fixture also passed both arms with zero
redundant tool calls. B0 used 88,221 cumulative model-input bytes; FBGC used
86,101 with 16 cumulative pointer appearances, a 2.4% byte reduction. This is
wiring evidence, not a real-token or cache result. The first real LongCat run
described earlier in this report predates the typed classification event and
must not be used to validate these TTL values.

The next real test should freeze this v2 binary and first run a purpose-built
archive-recovery task. It must require an early bulk read after GC and observe
either `memory_read` or automatic hydration. Only after that should the full
interleaved `B0/FBGC` Terminal-Bench comparison resume.
