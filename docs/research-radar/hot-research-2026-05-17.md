# Hot Research Implementation - 2026-05-17

## Summary

Implemented the feasible local subset from `Research Radar - 2026-05-17`.
The run focused on benchmark work that could be completed and verified without
external datasets, paid APIs, secrets, or unsafe security fixtures.

## Tasks Selected

- LongMemEval-V2 adapter spike:
  - added `benchmarks/longmemeval_v2/`
  - added synthetic Insert/Query memory fixture
  - added deterministic answer/evidence scorer
  - added unit and integration coverage
- Evidence-aware benchmark reporting:
  - added optional `EvidenceRecord`
  - added `BenchmarkReport.evidence_summary`
  - added aggregate score bounds for evidence-supported reporting
- Paper update:
  - added LongMemEval-V2 and evidence-bounds citations
  - added LongMemEval-V2 to the benchmark table
  - added a hot-research fixture coverage table
  - clarified the fixture-level limitation

## Tasks Skipped

- ComplexMCP full adapter:
  - skipped because the local run does not include the full benchmark release,
    stateful sandboxes, or failure-injection infrastructure
  - partial follow-up remains: build a smaller local ComplexMCP-style fixture
- AgentTelemetry span implementation:
  - skipped because the observability stack and OpenTelemetry integration are
    roadmap work, not present in this benchmark-only slice
- AgentTrap malicious skill fixture:
  - skipped because importing or recreating malicious skill cases needs license
    and safety review; this run avoided real side effects

## Files Changed

- `benchmarks/README.md`
- `benchmarks/core/__init__.py`
- `benchmarks/core/metrics.py`
- `benchmarks/core/runner.py`
- `benchmarks/core/types.py`
- `benchmarks/longmemeval_v2/`
- `tests/benchmarks/test_core.py`
- `tests/benchmarks/test_longmemeval_v2.py`
- `tests/integration/test_open_source_benchmark_integration.py`
- `paper/references.bib`
- `paper/sections/experiments.tex`
- `paper/sections/results.tex`
- `paper/sections/limitations.tex`
- `paper/tables/hot_research_benchmark_coverage.tex`
- `docs/research-radar/`
- `docs/hot-research/`

## Verification

| Command | Result |
| --- | --- |
| `uv run ruff check benchmarks/core benchmarks/longmemeval_v2 tests/benchmarks/test_core.py tests/benchmarks/test_longmemeval_v2.py tests/integration/test_open_source_benchmark_integration.py` | passed |
| `uv run pytest tests/benchmarks/test_core.py tests/benchmarks/test_longmemeval_v2.py -m unit` | 9 passed |
| `uv run pytest tests/benchmarks -m unit` | 30 passed |
| `uv run pytest tests/integration/test_open_source_benchmark_integration.py -m integration --run-integration` | 4 passed |
| `uv run python -m benchmarks.scripts.run_memory_baselines --benchmark locomo --format json` | FullText 1.0, NaiveRAG 1.0 on fixture |
| `uv run python -m benchmarks.scripts.run_memory_baselines --benchmark longmemeval --format json` | FullText 0.75, NaiveRAG 0.5 on fixture |
| `uv run python - <<'PY' ... longmemeval_v2 oracle fixture ... PY` | LongMemEval-V2 oracle fixture 1.0 on 3 cases |
| `make -C paper` | compiled `paper/main.pdf` |

## Benchmark Impact

- Local open-source benchmark fixture coverage improved from 2 adapters to 3:
  LongMemEval, LoCoMo, and new LongMemEval-V2.
- New LongMemEval-V2 fixture result:
  - `benchmark`: `longmemeval-v2:oracle-fixture`
  - `n_cases`: 3
  - `overall_score`: 1.0
  - per-ability scores: `cross-trajectory=1.0`,
    `environment-experience=1.0`, `safety-evidence=1.0`
- Existing memory baseline smoke results remain unchanged:
  - LoCoMo: FullText 1.0, NaiveRAG 1.0
  - LongMemEval: FullText 0.75, NaiveRAG 0.5
- This is a fixture-level coverage improvement, not a full-dataset accuracy
  improvement. No full LongMemEval-V2 upstream run was attempted because the
  complete dataset and evaluator were not imported into this repository.

## LaTeX Output

- PDF: `paper/main.pdf`
- Build command: `make -C paper`
- Build status: success
- Notes: LaTeX reports existing underfull/overfull box warnings and a preexisting
  BibTeX warning for `zhong2024memorybank`, but no unresolved citation warning
  remains after rerun.

## PR Notes

PR: https://github.com/haowei2000/Structure/pull/85

The PR targets `develop` from `hot-research` and emphasizes:

- benchmark fixture coverage improved from 2 to 3 local adapters
- LongMemEval-V2 adapter adds answer/evidence scoring
- benchmark reports can now expose evidence summaries and score bounds
- paper now includes a fixture-level hot-research coverage table

## Known Risks

- LongMemEval-V2 fixture is synthetic and should be replaced or supplemented
  after upstream schema and license review.
- Evidence bounds are optional metadata; full benchmark integrations must still
  attach real artifacts before score bounds are meaningful.
- ComplexMCP and AgentTrap remain tracked but intentionally unimplemented in
  this run due to infrastructure and safety constraints.
