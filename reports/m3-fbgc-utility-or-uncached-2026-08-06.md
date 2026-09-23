# M3 Gate — PGC/FBGC Improves Utility or Uncached Cost — 2026-08-06

## Decision

**M3: not met.**

File-backed GC can preserve task success and reduce provider-visible context
size, but on a prompt-caching provider the primary economic metric (uncached
input tokens) gets worse when GC admits. Utility was non-inferior on the valid
live sample, not improved.

## Gate definition

M3 passes only if at least one holds on a paired B0 vs FBGC (or PGC) comparison
with the same model, prompt, generation limits, and scorer:

1. **Utility improved**: higher task pass rate than B0, or
2. **Uncached cost improved**: lower success-conditioned uncached input tokens
   than B0 at non-inferior utility

Total input tokens, peak bytes, and cache-hit ratio are explanatory only.

## Blockers outside this run

- Harbor / Terminal-Bench could not be re-run here: the agent process cannot
  access `docker.sock` (`permission denied`).
- 1Password desktop integration was unavailable, so LongCat Harbor credentials
  were not injected. The live arm used the local DeepSeek OpenAI-compatible
  endpoint already configured in `.env`.

## Engineering changes made for the gate

- `harbor_agent` / `StructureAgent`: `--thinking` / `--no-thinking` (and
  `STRUCTURE_THINKING`) so Harbor can disable thinking as the Jul-31 notes
  recommended.
- `tier_b`: `--payload-bytes` for large single-message write payloads;
  `max_model_steps_per_run` scales with workload size.

## Experiments

### 1) Fixture A/B — wiring

- Artifact: `benchmarks/runtime-short-memory/results/2026-08-06-m3-fbgc-fixture.json`
- Workload: 12 `write_file` calls, 4096-byte payloads, strategies `B0,FBGC`
- Result: both 1/1 pass; FBGC model-input bytes **−8.1%**; fixture token
  estimates unchanged (fixture has no provider cache)

### 2) Live A/B — DeepSeek (valid M3 sample)

- Artifact: `benchmarks/runtime-short-memory/results/2026-08-06-m3-fbgc-live.json`
- Model: `deepseek-v4-flash` via OpenAI Chat Completions
- Workload: 12 `write_file` calls, 4096-byte payloads, one repetition each

| Strategy | Pass | Calls | Input | Cached | Uncached | Model-input bytes |
|---|---:|---:|---:|---:|---:|---:|
| B0 | 1/1 | 13 | 89,862 | 83,456 | **6,406** | 1,071,972 |
| FBGC | 1/1 | 13 | 84,142 | 75,392 | **8,750** | 974,458 |
| Delta | same | 0 | −6.4% | −9.7% | **+36.6%** | −9.1% |

FBGC first showed pointers on Provider call 11/13. That call reset cache
(`cached 7936 → 0`) and spent 6,764 uncached tokens in one step. Later calls
rebuilt cache on the smaller prompt, but the reset was not amortized.

### 3) Live stress — invalid for M3

- Artifact: `benchmarks/runtime-short-memory/results/2026-08-06-m3-fbgc-live-long.json`
- Workload: 20 files × 16 KiB embedded in the initial user JSON payload
- Both arms failed on the first Provider call (`error decoding response body`,
  ~300s). No utility or cost claim.

## Why uncached is hard under prompt caching

B0 keeps a large stable prefix that the provider caches (~93% cache hit on the
valid sample). FBGC removes middle `run_memory` batches and appends pointers,
which invalidates that prefix. The saved bytes therefore come mostly from
**cached** tokens, while the admission introduces a large **uncached** spike.

Mutation TTL (default 8) also delays FBGC eligibility, so the first admission
often occurs late in short tasks and leaves too few reuse steps.

## Relation to prior Jul-31 Harbor evidence

Matched WAL B0/FBGC already showed the same pattern: equal 3/5 success, nearly
identical uncached (−0.06%), and rare/late GC. Cython interleaved PGC and
post-fix FBGC arms previously increased uncached while shrinking total input.
This DeepSeek live sample reproduces the economic failure mode on a controlled
Rust-native workload.

## What would be required to reopen M3

1. Restore Docker access and run interleaved Harbor `B0,FBGC` with
   `--thinking false` on a long task where GC admits by ≤60% of the trajectory
   and ≥4 post-GC calls occur (existing `gc_quality_gate`).
2. Either measure on a provider without prompt caching, or change admission so
   a hot-cache reset is refused unless predicted post-admission uncached
   savings dominate the measured reset (stricter than today’s effort gate).
3. Keep success-conditioned uncached as the primary cost metric; do not
   substitute total input or peak bytes for M3.
