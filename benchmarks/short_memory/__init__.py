"""Short-memory regression benchmark.

Run in CI (LLM-free) to track the three-way tradeoff:
  P1 prompt-cache hit rate (head stability)
  P2 dedup ratio (avoid replaying redundant tool results)
  P3 GC yield (hot -> cold release to persistent storage)

Reuses the aggregate/report machinery in ``benchmarks.core`` for output, but
does **not** go through ``AgentProtocol`` — the replayed function
``_events_to_messages`` is exercised directly on fixture event logs.
"""
