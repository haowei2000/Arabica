"""Baseline agents used by the harness itself and by ablations.

* :class:`EchoAgent` --- deterministic, no-LLM; the harness regression tests
  drive it to verify runner/metric plumbing without paying for tokens.
"""

from benchmarks.baselines.echo_agent import EchoAgent

__all__ = ["EchoAgent"]
