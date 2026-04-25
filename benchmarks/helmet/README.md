# HELMET

Yen et al., *HELMET: How to Evaluate Long-Context Language Models
Effectively and Thoroughly*, ICLR 2025 (arXiv:2410.02694). Upstream
hint: `princeton-nlp/HELMET` (verify).

## Why this benchmark

Seven application-centric long-context categories (RAG, re-ranking,
citation generation, in-context learning, summarisation, QA,
safety) up to 128 K tokens. Fills the gap between synthetic probes
like RULER and task-shaped benchmarks --- strongest surface for
plotting the accuracy / token Pareto curve against flat-context
baselines.

## Planned layout

- `dataset.py` — one loader per sub-task (HELMET releases them as
  separate files); each loader produces `BenchmarkCase`s with the
  relevant long-context inputs under `inputs["context"]`.
- `scorer.py` — wraps HELMET's own metrics (model-based for some
  sub-tasks); guard the model-based variants behind an env var so CI
  stays deterministic.
- `fixtures/` — one small case per sub-task for smoke tests.

## Status

Stub. P1 priority: integrate after LongMemEval + LoCoMo so we have a
memory-first evidence set first, then add HELMET as the structured
long-context probe.
