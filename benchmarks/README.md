# Benchmark harness

Evaluation harness for the `paper/` claims about path-addressable,
multi-level context management. The design goal is a single surface
(`AgentProtocol` + `BenchmarkCase`) that accommodates every benchmark
listed in the paper's Experiments section, so a single runner can
produce comparable accuracy / token / step numbers across them.

## Layout

```
benchmarks/
├── core/              # Common types: BenchmarkCase, CostLedger, runner, metrics
├── baselines/         # EchoAgent + LightMem/MemBase reference baselines
├── scripts/           # Export/check scripts for benchmark artifacts
├── longmemeval/       # P0 adapter: loader + scorer + synthetic fixture
├── longmemeval_v2/    # P1 spike: memory-system loader + evidence scorer
├── locomo/            # P0 adapter: loader + scorer + synthetic fixture
├── helmet/            # P1 stub
├── taubench/          # P1 stub
└── ruler/             # P2 stub
```

Everything lives outside `src/structure/` on purpose: benchmarks are
evaluation infrastructure, not runtime code, and they should not
couple the platform's import graph to dataset-specific parsers.

## Status

| Benchmark | Priority | Adapter | Dataset | Scorer | Notes |
|-----------|:--:|:--:|:--:|:--:|---|
| LongMemEval | P0 | ✓ | fixture + upstream JSON | exact/substring | reference adapter |
| LongMemEval-V2 | P1 | spike | synthetic fixture | answer/evidence blend | Insert/Query memory-system shape |
| LoCoMo      | P0 | ✓ | fixture + upstream JSON/JSONL | exact/F1 | multi-session memory |
| LightMem/MemBase baselines | P0 | ✓ | reported LoCoMo table | source-table check | FullText, NaiveRAG, A-MEM, MemoryOS, Mem0, LangMem/EverMemOS catalog |
| HELMET      | P1 | stub | — | — | application long-context |
| τ-bench     | P1 | stub | — | — | tool + simulated user |
| RULER       | P2 | stub | — | — | synthetic NIAH-style |
| AgentBench, WebArena, SWE-bench, OSWorld, WorkArena, AppWorld, GAIA, AgentBoard, BFCL, LOFT | later | — | — | — | heavier infra |

Priorities align with `paper/sections/results.tex`: the P0s directly
exercise memory-level disclosure; the P1s let us plot the accuracy /
token Pareto; the P2+ entries validate generalisation across regimes.

## Quick start

```bash
# Run the harness self-test (uses EchoAgent + the bundled fixture).
pytest tests/benchmarks -m unit

# Export the LightMem/MemBase LoCoMo baseline table used by the paper.
python -m benchmarks.scripts.lightmem_baseline_report --format markdown

# Verify paper/tables/lightmem_locomo_baselines.tex matches the code data.
python -m benchmarks.scripts.lightmem_baseline_report \
  --check-paper-table paper/tables/lightmem_locomo_baselines.tex

# Run deterministic FullText/NaiveRAG memory baseline smoke tests.
python -m benchmarks.scripts.run_memory_baselines --benchmark locomo
python -m benchmarks.scripts.run_memory_baselines --benchmark longmemeval

# Run the LongMemEval-V2 adapter spike over its synthetic memory fixture.
pytest tests/benchmarks/test_longmemeval_v2.py -m unit

# Run a real LLM smoke test through an OpenAI-compatible endpoint.
# Do not commit the key; keep it in the shell environment.
export BENCHMARK_LLM_API_KEY=...
python -m benchmarks.scripts.run_llm_benchmark \
  --benchmark longmemeval \
  --model qwen-plus \
  --max-cases 4 \
  --format json

# Run sampled full-memory evaluation with extended efficiency metrics.
python -m benchmarks.scripts.run_full_memory_benchmark \
  --benchmark longmemeval-v2-small \
  --methods FullText NaiveRAG StructureMemory \
  --sample-percent 10 \
  --sample-mode hash \
  --sample-seed 2026-05-18 \
  --format json \
  --output benchmark_runs/lme-v2-small-10pct.json

# Score a fixture run end-to-end from Python:
python -c "
import asyncio
from benchmarks.baselines import EchoAgent
from benchmarks.core import BenchmarkRunner
from benchmarks.longmemeval import load_longmemeval, longmemeval_scorer

cases = load_longmemeval('benchmarks/longmemeval/fixtures/sample.json')
runner = BenchmarkRunner(
    benchmark_name='longmemeval-smoke',
    agent=EchoAgent(),          # oracle agent --- returns the reference
    scorer=longmemeval_scorer,
)
report = asyncio.run(runner.run(cases))
print(report.to_dict())
"
```

The oracle run is the sanity check: it should produce
`overall_score == 1.0` on the fixture. A real evaluation plugs a
Structure-backed agent into `BenchmarkRunner(agent=...)` and replaces
the fixture with the real LongMemEval dataset; see
`benchmarks/longmemeval/README.md`.

## Full-memory report metrics

`benchmarks.scripts.run_full_memory_benchmark` emits both headline scores and
diagnostics:

- accuracy, evidence pass/fail/unknown, score bounds
- prompt/completion/total tokens, latency, and optional USD cost
- `tokens_per_scored_point` and `latency_seconds_per_scored_point`
- dialogue `turn_count`, LME-V2 `trajectory_count`, and known `state_count`
- available/selected chunks, available/selected context tokens, and context
  compression ratio
- provider-reported KV-cache fields: `tokens_cached`,
  `cache_creation_tokens`, and `cache_read_tokens`
- accuracy buckets by context-token size, turn count, and trajectory count
- source-linked external baseline rows for calibration, including the
  LightMem/MemBase LoCoMo table and LongMemEval-V2 AgentRunbook-C rows

KV-cache counters depend on the OpenAI-compatible provider exposing cache
usage in the response. A zero value can mean either no cache hit or no reported
cache telemetry.

## Adding a new benchmark

1. Drop a directory under `benchmarks/<name>/` with at minimum a
   `README.md` and a loader that produces `BenchmarkCase` objects.
2. Implement a scorer `(reference, response) -> float` in `scorer.py`.
3. Add a fixture under `fixtures/` so the harness can self-test
   without downloading gigabytes of data.
4. Add one unit test under `tests/benchmarks/` that runs the
   `EchoAgent` through the adapter and asserts an oracle score of 1.0
   on the fixture --- this catches schema drift early.

The runner, metric aggregator, and cost ledger are benchmark-agnostic;
you should not have to touch `benchmarks/core/` to add a new benchmark.
