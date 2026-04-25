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
├── baselines/         # LLM-free agents used for harness self-tests (EchoAgent)
├── longmemeval/       # P0 adapter: loader + scorer + synthetic fixture
├── locomo/            # P0 stub (README only)
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
| LoCoMo      | P0 | stub | — | — | multi-session memory |
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
