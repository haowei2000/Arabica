"""Sequential benchmark runner.

Deliberately minimal: iterate cases, call the agent, collect results.
Parallel and resume-from-checkpoint variants live in follow-up work;
the paper's Experiments section starts from this same sequential loop
so results are comparable regardless of concurrency tuning.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from benchmarks.core.metrics import BenchmarkReport, Scorer, aggregate
from benchmarks.core.types import AgentProtocol, BenchmarkCase, BenchmarkResult

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkRunner:
    """Run an :class:`AgentProtocol` over a list of cases and produce a report.

    ``on_case`` is an optional callback invoked after each case completes
    (useful for progress bars and for streaming partial results to disk
    during long benchmark runs).  It receives ``(case, result)``.
    """

    benchmark_name: str
    agent: AgentProtocol
    scorer: Scorer
    on_case: Callable[[BenchmarkCase, BenchmarkResult], None] | None = None

    async def run(self, cases: Sequence[BenchmarkCase]) -> BenchmarkReport:
        results: list[BenchmarkResult] = []
        for case in cases:
            try:
                result = await self.agent.run(case)
            except Exception:
                logger.exception("agent failed on case %s", case.task_id)
                raise
            results.append(result)
            if self.on_case is not None:
                self.on_case(case, result)
        return aggregate(self.benchmark_name, cases, results, self.scorer)

    def run_sync(self, cases: Sequence[BenchmarkCase]) -> BenchmarkReport:
        """Convenience wrapper for callers outside an event loop."""
        return asyncio.run(self.run(cases))


def stream_from_iterable(cases: Iterable[BenchmarkCase]) -> list[BenchmarkCase]:
    """Helper that forces iterator materialisation exactly once.

    The runner needs to iterate cases twice (once to run, once to pair
    them with results in :func:`aggregate`), so callers who pass a
    generator must materialise first. This helper makes that explicit.
    """
    return list(cases)
