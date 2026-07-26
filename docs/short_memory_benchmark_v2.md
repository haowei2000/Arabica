# Runtime Short-Memory Benchmark v2

## Purpose

This benchmark evaluates Runtime's deterministic short-memory
materialisation contract:

```text
EventEnvelope[] + current_run_id + ShortMemoryPolicy
    -> ShortMemoryMaterialization {
         entries,
         batches,
         visibility,
       }
```

It does not evaluate long-memory retrieval, archive promotion, or provider
quality. Those are separate layers and require separate experiments.

The benchmark has two tiers:

1. **Tier A: deterministic runtime regression.** Replays immutable event
   traces without an LLM and measures correctness, compression, stability,
   and projector cost.
2. **Tier B: task-level validation.** Runs the same tasks with the same
   provider, model, runner, tool set, and seed while changing only the
   short-memory policy.

Tier A is required in CI. Tier B is required before making task-quality or
provider-cache claims in the paper.

## Boundary

Runtime GC changes only prompt visibility. It does not delete source events
and does not automatically move events into long memory.

```text
Session event log (immutable truth)
       |
       v
ShortMemoryProjector (TTL + relation decay + batching)
       |
       v
LOAD_ALL / LOAD_KEY / NO_LOAD
       |
       v
Provider-neutral RuntimeRequest
```

Promotion or archival into path-addressable long memory is an explicit,
separately measured operation.

## Three-Principle Mapping

| Principle | Benchmark interpretation | Primary metrics |
|---|---|---|
| P1: stable prompt head | Preserve reusable prefixes across consecutive turns | longest common prefix bytes/tokens, prefix reuse ratio, provider cache-read tokens in Tier B |
| P2: avoid repeated work | Keep enough relation and evidence state to prevent redundant actions | repeated tool-call rate, repeated path-read rate, completed-relation retention, task steps |
| P3: bounded hot memory | Reduce visible runtime history without mutating the audit log | materialised tokens, visible-event ratio, `LOAD_ALL` / `LOAD_KEY` / `NO_LOAD` distribution, projection latency |

P2 is not equivalent to byte-identical tool-result deduplication. Exact dedup
remains a historical baseline, while the v2 benchmark measures observable
redundant actions and relation-aware retention.

## Correctness Gates

An optimization result is invalid if any required gate fails:

1. **Source immutability:** input envelopes are byte-identical before and
   after materialisation.
2. **Determinism:** the same ordered trace, run id, and policy produce the same
   entries, batches, and visibility decisions.
3. **Provenance:** every materialised entry identifies its source event ids;
   every batch key covers exactly the events in that batch.
4. **Order preservation:** materialised entries preserve inherited-history
   order followed by local-history order.
5. **Relation integrity:** a visible tool result never becomes an unexplained
   provider item; full tool call/result pairs remain structurally encodable.
6. **Pinned-anchor retention:** events pinned by the evaluated policy remain
   visible.
7. **Fork replay equivalence:** replaying inherited plus local history produces
   the same materialisation as the canonical session assembly path.
8. **Semantic evidence recall:** full items retain their source evidence
   directly; compact keys count only when they contain all semantic fragments
   declared independently by the trace oracle. Provenance ids alone do not
   satisfy this gate. Reports include full/key counts and missing event ids.

## Compared Configurations

The harness compares policies over the same immutable trace. Baselines are
benchmark-only reference materialisers and must not change production code.

| ID | Configuration | Purpose |
|---|---|---|
| B0 | Full replay | Upper-bound context and audit-to-prompt baseline |
| B1 | Tail-K | Conventional sliding-window baseline |
| B2 | TTL only | Isolate event-level visibility from batch disclosure |
| B3 | Batch only | Isolate `LOAD_ALL` / `LOAD_KEY` / `NO_LOAD` from TTL decay |
| S | Structure default | TTL + relation-aware decay + batch disclosure |

Required ablations of `S`:

- no relation-aware decay;
- no recency floor;
- no pinned anchors;
- all visible batches use `LOAD_ALL`;
- binary disclosure (`LOAD_ALL` / `NO_LOAD`, without `LOAD_KEY`);
- TTL scale \(\beta \in \{0.5, 1, 2, 4, \infty\}\).

Named policy profiles such as conservative, default, and aggressive are
configuration presets, not distinct algorithms. Every result artifact must
contain the complete serialized policy and a stable policy hash.

## Trace Matrix

Tier A uses both synthetic and captured traces:

| Axis | Values |
|---|---|
| Trace length | 100, 1,000, 10,000, 100,000 events |
| Payload size | small messages, large tool results, mixed |
| Workload | repeated reads, edit/read cycles, tool failure/recovery, long multi-turn, fork/replay |
| Relation density | sparse, medium, dense call/result relations |
| Event noise | none, command-output bursts, lifecycle/control bursts |

Captured traces must be sanitized, versioned, and accompanied by a manifest
that records schema version and expected event counts. Synthetic generators
must record seed and generation parameters.

## Metrics

### Fidelity and behavior

- `anchor_recall`: retained required anchors divided by required anchors.
- `evidence_recall`: semantically retained gold evidence units divided by gold
  evidence units, split into full-item and compact-key retention;
- `missing_evidence_event_ids`: exact oracle evidence absent from both a full
  item and a semantically valid key;
- `relation_integrity_failures`: structurally invalid relation count.
- `redundant_tool_call_rate`: repeated equivalent actions divided by tool
  calls.
- `repeated_path_read_rate`: reads of an unchanged path repeated within the
  evaluation horizon.
- `task_score`, `steps`, and `tool_calls` for Tier B.

### Context efficiency

- source event count and estimated source tokens;
- visible event count and visible-event ratio;
- materialised entry count, bytes, and estimated tokens;
- materialised/source token ratio;
- batch count and token-weighted `LOAD_ALL`, `LOAD_KEY`, `NO_LOAD` ratios;
- batch-key overhead and average source events represented per key.

### Prefix stability

For consecutive compiled requests \(P_{t-1}\) and \(P_t\), measure the longest
common prefix directly:

```text
prefix_reuse_ratio_t = LCP_bytes(P[t-1], P[t]) / bytes(P[t-1])
```

Report the median, p95, and worst case. A body-size delta is not a valid proxy
for provider cache reuse. Tier B additionally records provider-reported cached
input tokens when available.

### Runtime cost

- projection wall time and nanoseconds per source event;
- p50, p95, and p99 over repeated runs;
- peak resident memory or allocated bytes when the benchmark environment can
  report them reliably;
- provider prompt tokens, time to first token, and total latency in Tier B.

Projector microbenchmarks run in release mode after warm-up and report machine,
OS, compiler, and git revision. Provider latency is reported separately because
network variance does not measure projector efficiency.

## Decision Rule

Do not use a single weighted short-memory score for the main claim. Report a
Pareto comparison after the correctness gates:

1. no correctness-gate failures;
2. Tier A reduces materialised tokens or projection cost relative to a baseline;
3. Tier B maintains task score within a declared tolerance while improving
   tokens per scored point or redundant-action rate;
4. conclusions hold on more than one trace family and are accompanied by
   confidence intervals or repeated-run dispersion.

A policy that is faster only because it loses required evidence is a failed
configuration, not a favorable efficiency result.

## Artifact Contract

Each run writes machine-readable JSON/JSONL containing:

- benchmark schema version;
- git revision and Rust compiler version;
- trace id, trace schema, seed, and source-event digest;
- full policy plus policy hash;
- current run id and fork lineage metadata;
- correctness-gate results;
- all raw per-step metrics and aggregate statistics.

Paper tables must be generated from these artifacts. Fixture tests and oracle
runs validate harness wiring but are not task-performance evidence.

## Implementation Sequence

1. **Implemented:** add a Rust-native benchmark data schema and deterministic
   synthetic trace generator, including fork lineage and reset child sequences.
2. **Implemented:** expose Runtime's full projection so baselines reuse the
   canonical event-to-memory mapping instead of duplicating production semantics.
3. **Implemented:** add correctness gates and B0/B1 reference materialisers.
4. **Implemented:** add B2 TTL-only, B3 Batch-only, full Structure policy,
   and release-mode scaling measurements.
5. **Implemented:** add an oracle-backed evidence-recall gate and configurable
   key admission/budget policy. The policy is deterministic and trace-driven;
   adaptive tuning remains deferred until captured and Tier-B evidence exists.
6. Add captured v3 event traces and fork/replay cases.
7. **Implemented harness:** connect Tier B to the real Session -> Runtime ->
   Provider -> Runner loop, including keyed evidence recall, exact file
   oracles, provider token accounting, and fixture/live evidence labels. Live
   repeated model artifacts are still required before calibrating the budget.
8. Generate paper tables only after artifacts are committed and reproducible.

The existing Python `benchmarks/short_memory/` package is retained as a legacy
v1 regression suite. It measures the removed Python message-replay path and
must not be presented as evidence for the Rust `ShortMemoryProjector`.

### Tier-B task contract

Each task runs in an isolated session and runner namespace:

1. a setup turn injects a keyed evidence value;
2. the evaluated turn supplies only that key and a relative output path;
3. Runtime projects the immutable setup events into short memory;
4. the provider must recall the value and emit a typed `write_file` call;
5. `LocalRunner` executes the call;
6. the oracle reads the actual file and requires an exact content match.

The harness records all provider calls across setup and evaluation, while task
success and tool-call limits are scoped to the evaluated run. It rejects an
output path that already exists, preventing stale files from contaminating the
oracle. The JSON artifact contains no provider secret and explicitly labels
deterministic fixture runs separately from live API runs. Because the current
provider interface is non-streaming, cached input tokens are recorded when the
wire response provides them, but time to first token remains unavailable.

### First live provider smoke: LongCat-2.0

A clean-revision Tier-B smoke run used the generic OpenAI Chat Completions
adapter against `https://api.longcat.chat/openai/v1` with model `LongCat-2.0`.
The passing task completed the two-turn keyed recall flow, emitted one typed
`write_file` call, and produced an exact on-disk content match. The provider
reported 1,085 input tokens, 288 output tokens, and 256 cached input tokens
across three calls; total provider latency was 48,584 ms. There were no tool
errors or redundant calls.

An immediately preceding diagnostic run expected the same semantic value plus
a trailing newline. The model wrote the value without that newline, so four of
five task checks passed while the exact file oracle failed. This is recorded as
an information-fidelity failure, not relabeled as success. The passing default
fixture therefore avoids incidental trailing whitespace; future
whitespace-sensitive workloads must declare it as part of the task semantics.

This is a one-task smoke result, not a policy comparison or paper-level quality
claim. It establishes real provider compatibility, provider-reported cache
accounting, short-memory recall, typed tool calling, and LocalRunner execution.
Repeated runs over multiple tasks and policies remain required.

### Implemented command surface

The Tier-A harness is the workspace package
`structure-short-memory-benchmark`. It emits a versioned JSON report and can
fail CI when a correctness gate fails:

```bash
cargo run -p structure-short-memory-benchmark -- \
  --baseline b0 --turns 100 --tools-per-turn 2 --fail-on-gate
```

B1 performs a structurally closed Tail-K selection: if the window contains a
tool result but truncates its matching call, the benchmark adds the earlier
call and preserves source order. Anchor and evidence loss are still reported;
structural repair does not turn a low-fidelity window into a passing result.

### Implemented ablation semantics

- **B2 TTL-only:** runs Runtime's event TTL and relation decay, does not build
  batch keys, and applies only the minimum tool-call closure required for a
  provider-encodable result sequence.
- **B3 Batch-only:** uses an unbounded event-visibility policy and applies the
  production batch disclosure rules. `Transient` and `Misc` batches can still
  become `NO_LOAD`; this is a batch decision, not TTL expiry.
- **S Structure:** runs the supplied serialized `ShortMemoryPolicy` through the
  complete production materialiser.

Synthetic gold evidence is bounded to a configurable recent-turn horizon. All
historical tool results remain in the immutable trace and relation oracle, but
they are not all declared necessary evidence for the next model decision.

### Key admission and evidence contract

`ShortMemoryPolicy.key_admission` optionally limits admitted historical keys by
batch count and total UTF-8 `key_content` bytes. Both limits are hard; `None`
means unbounded and preserves the pre-budget default. Candidate ordering is:

1. Runtime memory-class evidence value (`Anchor`, `Recovery`, `Working`,
   `Control`, `Transient`);
2. batch kind (`Turn`, `Context`, `Tool`, `Task`, `Artifact`, then noise);
3. recency in the supplied Session lineage order.

Each candidate records its rank, pre-admission key size, and one of `admitted`,
`rejected_batch_limit`, or `rejected_byte_limit`. Rejected batches become
`NO_LOAD`; the immutable source trace is unchanged.

The trace oracle separately stores required key fragments for every gold
evidence event. Synthetic tool-result evidence currently requires call id,
status, and a stable result fingerprint. A `BatchKey` that merely lists the
event in `source_event_ids` fails evidence recall if those fragments are absent.
This black-box check prevents provenance inflation from masking evidence loss.

### Release-mode scaling

```bash
cargo run --release -p structure-short-memory-benchmark -- \
  --scale-events 100,1000,10000,100000 \
  --warmup 5 --iterations 20
```

The timed region contains only projection. Trace generation, two-pass
correctness checks, JSON serialization, `rustc`/git environment discovery, and
report construction are excluded. Reports include the requested and actual
event counts, generated turn count, correctness status, materialised item
bytes, batch/load-state metrics, median/p95/p99 projection nanoseconds, and
median nanoseconds per source event. Debug builds are rejected by the command
surface for scaling runs.

> Engineering smoke note (2026-07-26): a low-iteration release run over
> approximately 100/1k/10k/100k events first exposed that replaying raw event
> JSON inside `BatchKey.key_content` could make B3 and Structure larger than
> full typed-item replay. Runtime now emits a bounded semantic index only for
> `LOAD_KEY` batches: result status and fingerprints are prioritized, field
> excerpts are capped at 64 characters, and the whole key at 384 characters.
> On the same 100k-event smoke configuration, Structure materialised bytes fell
> from 9,885,023 to 8,256,463 (-16.5%) while all correctness gates passed; B3
> fell from 21,696,728 to 16,695,997 (-23.0%). This remains a diagnostic result
> from an uncommitted, dirty worktree with three measured iterations, not a
> paper performance result. The next measured artifact run needs a clean
> revision, more repetitions, allocation/RSS metrics, and captured traces.

> Key-budget gate smoke note (2026-07-26): trace schema v3, benchmark-run v2,
> and scaling v2 add independent semantic evidence units, full/key retention,
> missing evidence ids, and key-admission metrics. On the same approximately
> 100k-event dirty-worktree trace, unbounded Structure had 14,298 key candidates
> and 8,256,463 materialised bytes. A 14,285-key budget rejected 13 lower-ranked
> keys, passed every gate, and materialised 8,248,754 bytes. Reducing the budget
> by one rejected 14 keys and failed semantic evidence recall at 50% while all
> 28,572 pinned anchors remained represented; the report identified the exact
> missing evidence event. Timing used only three repetitions and is not used to
> claim a performance change. This boundary run validates gate behavior, not an
> optimal production budget.
