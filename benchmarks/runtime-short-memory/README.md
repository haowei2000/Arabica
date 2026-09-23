# Runtime Short-Memory Benchmark

This Rust package implements the Tier-A and Tier-B benchmark harness described in
[`docs/short_memory_benchmark_v2.md`](../../docs/short_memory_benchmark_v2.md).

Implemented scope:

- versioned typed trace and oracle schema;
- deterministic synthetic event generator with stored generation parameters;
- forked traces with inherited/local lineage and reset child sequences;
- B0 Full Replay using Runtime's canonical event-to-memory mapping;
- B1 structurally closed Tail-K baseline;
- B2 TTL-only, B3 Batch-only, and full Structure policy projections;
- source immutability, determinism, provenance, order, tool relation, anchor,
  and oracle-backed semantic evidence-recall correctness gates;
- deterministic key admission with hard batch/content-byte budgets and
  explainable rejection decisions;
- machine-readable JSON reports.
- release-mode projector scaling with warm-up and p50/p95/p99 timing.
- a Tier-B `Session -> Runtime -> Provider -> LocalRunner` task loop with an
  exact file-content oracle and provider token accounting.

Run B0:

```bash
cargo run -p structure-short-memory-benchmark -- \
  --baseline b0 --turns 100 --tools-per-turn 2 --pretty --fail-on-gate
```

Run B1 with a 128-entry window:

```bash
cargo run -p structure-short-memory-benchmark -- \
  --baseline b1 --tail-k 128 --turns 100 --tools-per-turn 2 --pretty
```

Exercise fork replay ordering:

```bash
cargo run -p structure-short-memory-benchmark -- \
  --baseline b0 --turns 4 --fork-after-turn 2 --fail-on-gate
```

Run the three policy ablations:

```bash
cargo run -p structure-short-memory-benchmark -- --baseline b2 --turns 100
cargo run -p structure-short-memory-benchmark -- --baseline b3 --turns 100
cargo run -p structure-short-memory-benchmark -- --baseline s --turns 100
```

Run a budgeted Structure policy:

```bash
cargo run -p structure-short-memory-benchmark -- \
  --baseline s --turns 100 \
  --max-key-batches 64 --max-key-bytes 24576 \
  --fail-on-gate
```

The limits apply only to historical `LOAD_KEY` batches. Admission is stable:
memory-class evidence value, batch kind, then supplied-trace recency. A rejected
key becomes `NO_LOAD` with a recorded rank, candidate size, and rejection
reason. The benchmark does not count provenance alone as retained evidence: a
compact key must contain every semantic fragment declared independently by the
trace oracle. Reports split full/key evidence retention and list missing event
ids. An undersized budget therefore fails the gate instead of appearing to be
an efficiency win.

Run the full release-mode scaling matrix:

```bash
cargo run --release -p structure-short-memory-benchmark -- \
  --scale-events 100,1000,10000,100000 \
  --warmup 5 --iterations 20 --pretty
```

Scaling measures `Baseline::project` only. Trace generation, correctness
checks, environment discovery, and JSON serialization run outside the timed
region. Target event counts are lower bounds because the generator emits only
complete typed turns; every sample reports its actual event count. Materialised
bytes serialize `ShortMemoryItem` values only, excluding benchmark provenance
and sequence fields that are not sent as model context.

B1 may intentionally fail anchor or evidence gates when the window is too
small. The JSON report is still emitted so the failure is measurable;
`--fail-on-gate` turns such a result into a non-zero process exit for CI.

## Tier B

The Tier-B task uses two turns in one session. The setup turn stores a keyed
evidence value. The evaluated turn contains only the key and destination path,
so the provider must recover the value from Runtime's short-memory projection,
call `write_file`, and finish after the real `LocalRunner` succeeds. A final
claim without an exact on-disk content match fails the task.

Run the deterministic wiring fixture without a network request:

```bash
cargo run -p structure-short-memory-benchmark --bin tier_b -- \
  --fixture --repetitions 3 --pretty --fail-on-task
```

Run against an OpenAI-compatible endpoint after injecting the credential into
`OPENAI_API_KEY` or `OPENAI__API_KEY` through the process environment:

```bash
cargo run -p structure-short-memory-benchmark --bin tier_b -- \
  --model <MODEL> --repetitions 3 --pretty --fail-on-task
```

The report is versioned as `structure.short-memory.tier-b/v1` and stores the
serialized policy, exact task checks, provider calls, input/output/cached-input
tokens, provider latency, tool counts, redundant calls, full protocol events,
and environment metadata. It never serializes the API key. Fixture reports are
marked `evidence_level: fixture`; only reports marked `live_api` are real model
evidence. The current provider path is non-streaming, so it reports total
provider latency but not time to first token.

The default evidence value intentionally has no trailing whitespace. A live
LongCat smoke run preserved the semantic value but normalized away a trailing
newline, which the exact oracle correctly rejected. Whitespace-sensitive tasks
should declare that requirement explicitly instead of relying on an incidental
line ending.

Committed live artifacts and their evidence limitations are indexed under
[`results/`](results/README.md).
