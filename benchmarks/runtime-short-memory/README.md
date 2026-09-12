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

Runtime also applies a per-batch benefit gate before key admission. The default
key-content budget is the smaller of 384 bytes and 60% of the batch's serialized
full model-item bytes. A BatchKey is used only when the complete serialized key
item is strictly smaller than the corresponding full items; otherwise the batch
stays `LOAD_ALL` with a `kept_full_no_benefit` diagnostic. Benchmark reports use
serialized model-input bytes and estimated tokens as the compression gate and
expose Full Replay bytes, bytes saved, and input-size basis points. Entry count
is diagnostic only. The Runtime gate is provider-neutral: Tier-B v3 also
records the complete provider-neutral request size and provider-reported token
usage so codec expansion can be detected separately. A smaller
`ShortMemoryItem` does not by itself guarantee fewer tokens after a provider
maps a BatchKey to wire messages.

`BatchKey` remains an intentional benchmark/ablation representation. The new
`FileBackedGC` Runtime strategy does not use it. TTL expiry creates GC
eligibility only; a batch must also be fully expired, relation-closed, outside
the protected working tail, and strictly smaller as a pointer before it can be
compacted. Exact canonical event envelopes are written to the configured file
archive and SHA-256-verified before Provider projection changes. Only complete
epochs are then replaced by `MemoryPointer` values. The open epoch stays
append-only and lossless. A complete epoch is only collected when its
probability-weighted future token savings cover the estimated uncached-token
cost of a cache reset multiplied by `COMPACTION_EFFORT`. Runtime uses the preceding
real Provider response's `cached_input_tokens` as reset cost when usage is
available, and falls back to the projected stable-prefix token count only when
it is not. Removable bytes are converted with the preceding request's observed
token density. Remaining calls use a bounded geometric survival curve controlled by
`PGC_CONTINUATION_PROBABILITY_BPS` (default 7,500) instead of using checkpoint
batch count as a horizon proxy. `COMPACTION_EFFORT=1` admits estimated break-even
collection; larger positive integers require proportionally more return.
Admission checks and accepted epoch transitions are included in Harbor v6
reports.
Pointers use meaningful logical paths such as
`m/tool/write_file/<sha256>.json`; the file adapter maps that path beneath its
configured root, while SQLite uses the same path as its primary key. Absolute
host paths are never exposed to the model.

MemoryPointer values are encoded as a compact Provider-visible suffix containing
the relative path, content hash, relation metadata, and exact `memory_read`
argument. `memory_search` returns matching logical paths and `memory_read`
verifies and hydrates one exact archive when the model needs older evidence.
Both tool schemas are present from the first call so enabling recovery does not
mutate the reusable prompt prefix.

Tier-B v5 keeps legacy `PGC` for comparison and adds independent `FBGC`:
lossless file-backed compact over only fully TTL-expired closed batches, with
no BatchKey. Reports record the selected Runtime compaction strategy and count
BatchKey and MemoryPointer appearances separately.

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

Tier B has two explicitly different workload shapes. The default memory-recall
workload uses multiple user messages in one session: a setup message stores a
keyed evidence value, optional distractor messages build chat history, and the
evaluated message contains only the key and destination path. It exercises
Runtime's short-memory projection. A final claim without an exact on-disk
content match fails the task.

The single-message agent workload sends exactly one user message and requires
multiple `write_file` calls within the same run. It exercises the real
`Provider -> Runtime continuation loop -> LocalRunner` path. Session persists
each run event immediately, and Runtime reprojects that canonical history
before every model step. The latest tool step remains lossless continuation;
older closed tool batches become `run_memory` and are eligible for TTL,
`LOAD_KEY`, or `NO_LOAD` under the pure policy. In CoreRuntime those closed
batches then pass through checkpoint PointerGC, while the latest pair remains
unchanged in continuation. This workload is therefore the
end-to-end check for active-run projection and recoverable archival, not a
chat-history turn-count test.

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

Run a true one-user-message long agent task with twelve exact tool calls:

```bash
cargo run -p structure-short-memory-benchmark --bin tier_b -- \
  --single-message-tools 12 --model <MODEL> --pretty --fail-on-task
```

Add `--compare` to execute B0, B2, B3, S, PGC, and FBGC over that same single-message
task. Runtime permits at most 32 model steps per run, so the benchmark remains
bounded while allowing this workload to cross the default 20-event recency
floor.

Use `--strategies B0,FBGC` with `--compare` for a focused live A/B. This avoids
spending provider calls on unrelated ablations while measuring cache behavior.

Run the end-to-end policy comparison against the real provider:

```bash
cargo run -p structure-short-memory-benchmark --bin tier_b -- \
  --compare --model <MODEL> --repetitions 3 --pretty --fail-on-task
```

Comparison mode executes B0 Full Replay, B2 TTL-only, B3 Batch-only, and the
production Structure policy through the same
`SessionManager -> CoreRuntime -> Provider -> LocalRunner` path. Every strategy
gets the same task definitions, a fresh provider, and an isolated runner root.
By default, eight unrelated setup turns push the required evidence beyond the
recent full-history window; override this with `--history-turns <N>`. Each of
these turns is a separate user message; it is chat-history stress, not an
internal step of one agent task.
The versioned comparison report groups task success, tokens, provider latency,
and tool-call metrics by strategy. B1 Tail-K is deliberately excluded because
Tail-K is not an executable Runtime policy; including it would not be a genuine
end-to-end comparison. Use `--compare --fixture` to validate comparison wiring
without network calls.

The report is versioned as `structure.short-memory.tier-b/v5` and stores the
serialized policy, exact task checks, provider calls, input/output/cached-input
and uncached-input tokens, provider latency, tool counts, redundant calls,
accepted user-message counts, historical/run-memory bytes, continuation item
counts and bytes, complete provider-neutral model-input bytes, full protocol
events, PointerGC activation counts, and environment metadata. It never
serializes the API key. Fixture reports are
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

## Rust external-agent binary

The `harbor_agent` binary and its transport protocol remain part of this Rust
package. The Python Harbor bridge, campaign controller and preflight tools
were removed at the Rust-only cutover. This repository no longer provides a
runnable `harbor run` integration; an external host must implement the transport.

```bash
cargo build --release -p structure-short-memory-benchmark --bin harbor_agent
```

The following Harbor report and campaign notes describe historical runs and
the retained Rust reporting contract, not a currently supplied orchestrator.

Credentials must be injected into the Harbor host process. Do not place them in
the job configuration. The agent writes `structure-report.json` on normal
completion and `provider-calls.partial.json` after every Provider response so
timeouts retain token/cache evidence. FBGC also writes
`pointer-gc-admissions.partial.json` after every eligibility decision so an
external Harbor cancellation preserves the estimated reset cost, weighted
horizon, and admission reason. B0 and FBGC must use identical model, prompt,
timeout, task image, and generation settings.

Primary B0/FBGC blocks also assign the same sampling seed to both treatments
and randomize only their execution order. The frozen primary decision uses
five repetitions per task. It admits cost evidence only from pairs where both
arms pass without infrastructure failure, requires at least five such pairs,
requires the FBGC trajectory gate, applies a 10 percentage-point quality
non-inferiority margin, and accepts cost improvement only when the geometric
mean fresh-input reduction is at least 10% and its deterministic
10,000-resample paired-bootstrap 95% confidence interval excludes no change.

Every Harbor run also retains lossless raw exchanges before any parsing or
context truncation:

- `provider-raw/<sequence>-<run-id>/request.raw.json` is the exact JSON body
  sent to the Provider;
- `provider-raw/<sequence>-<run-id>/response.raw` is the exact HTTP body
  received from the Provider;
- `tool-raw/<sequence>-<call-id>/request.raw.json`, `stdout.raw`, and
  `stderr.raw` preserve the complete shell exchange even when the event-history
  projection uses a bounded tool result.

Provider raw capture never writes the Authorization header or API key. These
artifacts can still contain task data, prompts, model reasoning fields, and
tool output, so they belong in the private Harbor job directory under
`target/` and must not be committed.

Harbor report schema `structure.harbor-agent/v9` includes aggregate reasoning
output tokens and the `gc_quality_gate` for
long-task FBGC experiments. The gate passes only when the first admitted GC is
at or before 60% of the final Provider trajectory, the compacted projection is
used for at least four Provider calls, and an exact auto-hydration reads an
archive at a strictly later model step. This is a trajectory gate, not a task
quality result: formal acceptance must additionally require Runtime terminal
success, Harbor reward 1, and all verifier checks.
