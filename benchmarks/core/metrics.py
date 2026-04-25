"""Aggregate a batch of :class:`BenchmarkResult` into a report.

The runner collects results and a scorer callable; this module reduces
them to the numbers that go into the paper's main-results and ablation
tables: overall accuracy, per-ability accuracy, and mean cost.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import mean
from typing import Callable, Iterable, Mapping

from benchmarks.core.types import BenchmarkCase, BenchmarkResult, CostLedger

# A scorer takes the gold reference from the case and the agent's
# response and returns a scalar in [0, 1].
Scorer = Callable[[object, object], float]


@dataclass
class BenchmarkReport:
    """Headline numbers for a benchmark run."""

    benchmark: str
    n_cases: int
    overall_score: float
    per_ability_score: Mapping[str, float]
    mean_cost: CostLedger
    total_cost: CostLedger
    per_case: list[tuple[str, float, CostLedger]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "benchmark": self.benchmark,
            "n_cases": self.n_cases,
            "overall_score": round(self.overall_score, 4),
            "per_ability_score": {
                k: round(v, 4) for k, v in self.per_ability_score.items()
            },
            "mean_cost": self.mean_cost.__dict__,
            "total_cost": self.total_cost.__dict__,
        }


def aggregate(
    benchmark_name: str,
    cases: Iterable[BenchmarkCase],
    results: Iterable[BenchmarkResult],
    scorer: Scorer,
) -> BenchmarkReport:
    """Pair cases with results by ``task_id`` and produce a :class:`BenchmarkReport`."""
    cases_by_id = {c.task_id: c for c in cases}
    by_ability: dict[str | None, list[float]] = defaultdict(list)
    all_scores: list[float] = []
    per_case: list[tuple[str, float, CostLedger]] = []
    total = CostLedger()

    for result in results:
        case = cases_by_id.get(result.task_id)
        if case is None:
            raise KeyError(f"Result references unknown task_id {result.task_id!r}")
        score = float(scorer(case.reference, result.response))
        all_scores.append(score)
        by_ability[case.ability].append(score)
        per_case.append((result.task_id, score, result.cost))
        total = total + result.cost

    if not all_scores:
        return BenchmarkReport(
            benchmark=benchmark_name,
            n_cases=0,
            overall_score=0.0,
            per_ability_score={},
            mean_cost=CostLedger(),
            total_cost=CostLedger(),
            per_case=[],
        )

    mean_cost = CostLedger(
        tokens_prompt=total.tokens_prompt // len(all_scores),
        tokens_completion=total.tokens_completion // len(all_scores),
        steps=total.steps // len(all_scores),
        tool_calls=total.tool_calls // len(all_scores),
        latency_seconds=total.latency_seconds / len(all_scores),
        usd_cost=total.usd_cost / len(all_scores),
    )

    per_ability = {
        (ability or "_all"): mean(scores)
        for ability, scores in by_ability.items()
        if scores
    }

    return BenchmarkReport(
        benchmark=benchmark_name,
        n_cases=len(all_scores),
        overall_score=mean(all_scores),
        per_ability_score=per_ability,
        mean_cost=mean_cost,
        total_cost=total,
        per_case=per_case,
    )
