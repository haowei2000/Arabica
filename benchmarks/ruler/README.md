# RULER

Hsieh et al., *RULER: What's the Real Context Size of Your Long-Context
Language Models?*, COLM 2024 (arXiv:2404.06654). Upstream hint:
`NVIDIA/RULER` (verify).

## Why this benchmark

Synthetic benchmark extending needle-in-a-haystack with multi-hop
tracing, aggregation, and QA across configurable lengths up to 128 K+.
Cheap to run, fully controllable, and ideal for ablating the
GLANCE / OVERVIEW / DETAIL disclosure levels in isolation.

## Planned layout

- `dataset.py` — `RULER` does not ship a fixed JSON; it provides a
  generator script parametrised by length and task. Adapter wraps the
  generator and emits `BenchmarkCase`s with `metadata["length_tokens"]`
  so the aggregator can bucket results by context length.
- `scorer.py` — retrieval accuracy per sub-task; deterministic.
- `fixtures/` — a tiny 512-token / 1-needle fixture for CI.

## Status

Stub. P2 priority: use it for ablation-only plots once the core
memory benchmarks are running.
