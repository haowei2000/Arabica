# Full Memory Benchmark Readiness - 2026-05-17

## Scope

This run added the executable path for full LoCoMo, LongMemEval-S/M, and
LongMemEval-V2 evaluation with a fixed OpenAI-compatible reader model and a
fixed deterministic normalized-match judge.

Reader configuration was loaded from the user's shell:

- `BENCHMARK_LLM_BASE_URL`: `https://token-plan-cn.xiaomimimo.com/v1`
- `BENCHMARK_LLM_MODEL`: `mimo-v2.5-pro`
- `BENCHMARK_LLM_API_KEY`: set, not recorded

## Data Imported

Downloaded public core files under ignored local data paths:

| Benchmark | Local path | Cases |
| --- | --- | ---: |
| LoCoMo | `benchmarks/data/locomo/locomo10.json` | 1986 |
| LongMemEval-S | `benchmarks/data/longmemeval/longmemeval_s_cleaned.json` | 500 |
| LongMemEval-M | `benchmarks/data/longmemeval/longmemeval_m_cleaned.json` | 500 |
| LongMemEval-V2 Small | `benchmarks/data/longmemeval_v2/` | 451 |
| LongMemEval-V2 Medium | `benchmarks/data/longmemeval_v2/` | 451 |

LongMemEval-V2 `trajectories.jsonl` is indexed by
`benchmarks/data/longmemeval_v2/trajectories.offsets.json` so cases can lazy
load only referenced trajectories. Screenshot tarballs were not downloaded
because the current reader path is text-only; multimodal visual evidence should
be reported as unknown until a vision reader is wired.

## Methods Implemented

| Method | Implementation |
| --- | --- |
| FullText | Sends all available case memory context, truncated by `--max-context-chars`, to the fixed reader. |
| NaiveRAG | Lexically selects top-k chunks and sends them to the fixed reader. |
| StructureMemory | Inserts chunks into Structure's file-based context service, retrieves top-k chunks, then sends them to the same fixed reader. |

All methods report:

- accuracy from the fixed deterministic scorer
- mean and total latency
- prompt/completion/total tokens
- optional USD cost when per-million-token rates are supplied
- evidence pass/unknown/fail summary and score bounds
- per-case response JSONL under `benchmark_runs/*-cases.jsonl`

## Preflight Results

These are one-case API checks, not full benchmark claims.

| Benchmark | Method | Cases | Accuracy | Evidence | Prompt tokens | Completion tokens | Latency |
| --- | --- | ---: | ---: | --- | ---: | ---: | ---: |
| LoCoMo | FullText | 1 | 1.0000 | pass=1 | 13,587 | 275 | 7.750s |
| LoCoMo | NaiveRAG | 1 | 0.4286 | pass=1 | 3,933 | 154 | 4.214s |
| LoCoMo | StructureMemory | 1 | 0.4286 | pass=1 | 3,929 | 137 | 5.258s |
| LongMemEval-S | FullText | 1 | 1.0000 | pass=1 | 25,604 | 84 | 5.038s |
| LongMemEval-S | NaiveRAG | 1 | 1.0000 | pass=1 | 17,504 | 79 | 4.579s |
| LongMemEval-S | StructureMemory | 1 | 1.0000 | pass=1 | 17,456 | 118 | 6.060s |
| LongMemEval-V2 Small | FullText | 1 | 1.0000 | pass=1 | 39,515 | 2,989 | 49.433s |
| LongMemEval-V2 Small | NaiveRAG | 1 | 1.0000 | pass=1 | 39,732 | 1,605 | 32.469s |
| LongMemEval-V2 Small | StructureMemory | 1 | 1.0000 | pass=1 | 39,720 | 2,104 | 41.440s |

## Full Run Commands

Run these from a shell that sources `.zshrc`:

```bash
zsh -ic 'uv run python -m benchmarks.scripts.run_full_memory_benchmark --benchmark locomo --format json --output benchmark_runs/full-locomo.json'
zsh -ic 'uv run python -m benchmarks.scripts.run_full_memory_benchmark --benchmark longmemeval-s --format json --output benchmark_runs/full-longmemeval-s.json'
zsh -ic 'uv run python -m benchmarks.scripts.run_full_memory_benchmark --benchmark longmemeval-m --format json --output benchmark_runs/full-longmemeval-m.json'
zsh -ic 'uv run python -m benchmarks.scripts.run_full_memory_benchmark --benchmark longmemeval-v2-small --format json --output benchmark_runs/full-longmemeval-v2-small.json'
zsh -ic 'uv run python -m benchmarks.scripts.run_full_memory_benchmark --benchmark longmemeval-v2-medium --format json --output benchmark_runs/full-longmemeval-v2-medium.json'
```

The preflight suggests the full API run is large. Extrapolating only from the
benchmarks actually preflighted:

| Benchmark | Estimated tokens across 3 methods | Estimated wall time |
| --- | ---: | ---: |
| LoCoMo | 43,721,790 | 9.50h |
| LongMemEval-S | 30,422,500 | 2.18h |
| LongMemEval-V2 Small | 56,674,915 | 15.45h |

LongMemEval-M and LongMemEval-V2 Medium were not extrapolated from live samples
in this run, but they are expected to be at least as expensive as their smaller
counterparts under the current 120k-character truncation policy. The complete
five-benchmark run should be launched only with explicit cost/time approval.

## Verification

- `uv run ruff check benchmarks/baselines benchmarks/adapters benchmarks/locomo benchmarks/longmemeval_v2 benchmarks/scripts/prepare_full_datasets.py benchmarks/scripts/run_full_memory_benchmark.py`
- `uv run pytest tests/benchmarks/test_core.py tests/benchmarks/test_memory_baseline_runner.py tests/benchmarks/test_llm_benchmark.py tests/benchmarks/test_locomo.py tests/benchmarks/test_longmemeval_v2.py -m unit`
