"""Aggregate a batch of :class:`BenchmarkResult` into a report.

The runner collects results and a scorer callable; this module reduces
them to the numbers that go into the paper's main-results and ablation
tables: overall accuracy, per-ability accuracy, and mean cost.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from statistics import mean

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
    evidence_summary: Mapping[str, int] = field(default_factory=dict)
    score_bounds: tuple[float, float] | None = None

    def to_dict(self) -> dict:
        payload = {
            "benchmark": self.benchmark,
            "n_cases": self.n_cases,
            "overall_score": round(self.overall_score, 4),
            "per_ability_score": {
                k: round(v, 4) for k, v in self.per_ability_score.items()
            },
            "mean_cost": self.mean_cost.__dict__,
            "total_cost": self.total_cost.__dict__,
        }
        if self.evidence_summary:
            payload["evidence_summary"] = dict(self.evidence_summary)
        if self.score_bounds is not None:
            payload["score_bounds"] = tuple(round(v, 4) for v in self.score_bounds)
        return payload


def _default_score_bounds(status: str, score: float) -> tuple[float, float]:
    """Derive conservative bounds when a result has evidence metadata.

    ``pass`` means the observed scalar score is evidence-supported.
    ``fail`` means the evidence contradicts the claim, so the supported
    score is zero. ``unknown`` means the evidence is incomplete and the
    true score lies anywhere in ``[0, score]``.
    """
    if status == "pass":
        return score, score
    if status == "fail":
        return 0.0, 0.0
    return 0.0, score


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
    evidence_summary: dict[str, int] = defaultdict(int)
    lower_bounds: list[float] = []
    upper_bounds: list[float] = []

    for result in results:
        case = cases_by_id.get(result.task_id)
        if case is None:
            raise KeyError(f"Result references unknown task_id {result.task_id!r}")
        score = float(scorer(case.reference, result.response))
        all_scores.append(score)
        by_ability[case.ability].append(score)
        per_case.append((result.task_id, score, result.cost))
        total = total + result.cost
        if result.evidence is not None:
            evidence_summary[result.evidence.status] += 1
            bounds = result.evidence.score_bounds or _default_score_bounds(
                result.evidence.status,
                score,
            )
            lower_bounds.append(bounds[0])
            upper_bounds.append(bounds[1])

    if not all_scores:
        return BenchmarkReport(
            benchmark=benchmark_name,
            n_cases=0,
            overall_score=0.0,
            per_ability_score={},
            mean_cost=CostLedger(),
            total_cost=CostLedger(),
            per_case=[],
            evidence_summary={},
            score_bounds=None,
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
    score_bounds = (
        (mean(lower_bounds), mean(upper_bounds))
        if lower_bounds and upper_bounds
        else None
    )

    return BenchmarkReport(
        benchmark=benchmark_name,
        n_cases=len(all_scores),
        overall_score=mean(all_scores),
        per_ability_score=per_ability,
        mean_cost=mean_cost,
        total_cost=total,
        per_case=per_case,
        evidence_summary=dict(evidence_summary),
        score_bounds=score_bounds,
    )
