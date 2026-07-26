# Runtime Short-Memory Benchmark

This Rust package implements the deterministic Tier-A benchmark described in
[`docs/short_memory_benchmark_v2.md`](../../docs/short_memory_benchmark_v2.md).

Implemented scope:

- versioned typed trace and oracle schema;
- deterministic synthetic event generator with stored generation parameters;
- forked traces with inherited/local lineage and reset child sequences;
- B0 Full Replay using Runtime's canonical event-to-memory mapping;
- B1 structurally closed Tail-K baseline;
- B2 TTL-only, B3 Batch-only, and full Structure policy projections;
- source immutability, determinism, provenance, order, tool relation, anchor,
  and evidence-recall correctness gates;
- machine-readable JSON reports.
- release-mode projector scaling with warm-up and p50/p95/p99 timing.

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
