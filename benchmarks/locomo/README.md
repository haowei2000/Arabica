# LoCoMo

Maharana et al., *Evaluating Very Long-Term Conversational Memory of
LLM Agents*, ACL 2024 (arXiv:2402.17753). Upstream hint:
`snap-research/locomo` (verify before cloning).

## Why this benchmark

Multi-session dialogues averaging 300 turns / 9 K tokens across up to
35 sessions, with QA, summarisation, and multi-modal generation
sub-tasks. Directly tests the persistent-workspace-context claim
(does ContextStore survive dozens of sessions?).

## Layout

- `dataset.py` — parses conversation-centric JSON/JSONL and expands
  each QA pair into a `BenchmarkCase`. `inputs["sessions"]` mirrors
  LongMemEval for cross-benchmark reuse.
- `scorer.py` — deterministic exact/substring match with token-F1
  fallback for the QA sub-task. LLM-judge scoring for open-ended
  summary tasks can be swapped in later with the same scorer signature.
- `fixtures/sample.json` — synthetic multi-session conversations for
  the harness self-test.

## Status

P0 adapter is live for QA-style LoCoMo cases. Run:

```bash
pytest tests/benchmarks/test_locomo.py -q
python -m benchmarks.scripts.run_memory_baselines --benchmark locomo
```

The bundled runner uses deterministic FullText/NaiveRAG retrieval
smoke baselines and does not call an LLM.
