"""Deterministic, LLM-free agent used for harness self-tests.

Returns a caller-supplied transform of the case inputs, counts one "step",
and attributes zero tokens.  Swapping in this agent lets the runner,
metric aggregator, and per-benchmark scorer be exercised end-to-end in
CI without touching an LLM endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from benchmarks.core.types import BenchmarkCase, BenchmarkResult, CostLedger


@dataclass
class EchoAgent:
    """An :class:`AgentProtocol` that returns ``policy(case)``.

    ``policy`` defaults to "echo the reference", which gives a perfect
    score on exact-match scorers --- handy for verifying that the
    scorer wiring is correct.  Tests override ``policy`` to simulate
    imperfect agents.
    """

    policy: Callable[[BenchmarkCase], object] = lambda case: case.reference

    async def run(self, case: BenchmarkCase) -> BenchmarkResult:
        return BenchmarkResult(
            task_id=case.task_id,
            response=self.policy(case),
            cost=CostLedger(steps=1),
        )
