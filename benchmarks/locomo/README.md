# LoCoMo

Maharana et al., *Evaluating Very Long-Term Conversational Memory of
LLM Agents*, ACL 2024 (arXiv:2402.17753). Upstream hint:
`snap-research/locomo` (verify before cloning).

## Why this benchmark

Multi-session dialogues averaging 300 turns / 9 K tokens across up to
35 sessions, with QA, summarisation, and multi-modal generation
sub-tasks. Directly tests the persistent-workspace-context claim
(does ContextStore survive dozens of sessions?).

## Planned layout

- `dataset.py` — parse LoCoMo's conversation + QA JSONL. Each case
  becomes a `BenchmarkCase` whose `inputs["sessions"]` mirrors
  LongMemEval for cross-benchmark reuse.
- `scorer.py` — ROUGE-L / exact-match for the QA sub-task; BLEURT or
  LLM judge for the open-ended summary sub-task (optional, gated).
- `fixtures/sample.json` — two synthetic multi-session conversations
  for the harness self-test.

## Status

Stub. Pick this up after LongMemEval end-to-end integration is live,
since the schema is similar enough that the loader can share helpers.
