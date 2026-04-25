# τ-bench

Yao et al., *τ-bench: A Benchmark for Tool-Agent-User Interaction in
Real-World Domains*, arXiv:2406.12045 (2024; no peer venue confirmed).
Upstream hint: `sierra-research/tau-bench` (verify).

## Why this benchmark

Retail and airline customer-service domains where a simulated user
chats with a tool-using agent; success is measured by final database
state plus the `pass^k` consistency metric. Directly exercises
Structure's tool registry + function-calling strategy under a long
multi-turn tool loop. `pass^k` also captures the stability gains we
expect from the context-source rating + event-level GC features.

## Planned layout

- `dataset.py` — τ-bench ships per-domain task lists as Python
  modules; the loader imports them and converts each task into a
  `BenchmarkCase` whose `inputs` holds the initial DB state and the
  user-simulator seed.
- `scorer.py` — compares post-run DB state with the expected final
  state; wraps the upstream `pass` and `pass^k` metrics.
- A dedicated agent adapter is required because τ-bench runs a
  live simulated user loop; it won't fit the stateless `AgentProtocol`
  and will need a variant interface (planned under
  `benchmarks/core/interactive.py` when we take this on).

## Status

Stub. P1 priority but the most code to write because of the
user-simulator loop; start LongMemEval first.
