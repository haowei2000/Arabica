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
from typing import Any

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
    per_case_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    evidence_summary: Mapping[str, int] = field(default_factory=dict)
    score_bounds: tuple[float, float] | None = None
    diagnostic_summary: Mapping[str, Any] = field(default_factory=dict)

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
        if self.diagnostic_summary:
            payload["diagnostic_summary"] = dict(self.diagnostic_summary)
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


def _numeric(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _mean_field(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [_numeric(row.get(field)) for row in rows]
    present = [value for value in values if value is not None]
    if not present:
        return None
    return mean(present)


def _bucket_context_tokens(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value <= 4_000:
        return "<=4k"
    if value <= 16_000:
        return "4k-16k"
    if value <= 64_000:
        return "16k-64k"
    if value <= 128_000:
        return "64k-128k"
    return ">128k"


def _bucket_count(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value == 0:
        return "0"
    if value <= 10:
        return "1-10"
    if value <= 50:
        return "11-50"
    if value <= 200:
        return "51-200"
    if value <= 500:
        return "201-500"
    return ">500"


def _bucketed_accuracy(
    rows: list[dict[str, Any]],
    field: str,
    bucket_fn: Callable[[float | None], str],
) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        grouped[bucket_fn(_numeric(row.get(field)))].append(float(row["score"]))
    return {
        bucket: {"n": len(scores), "accuracy": round(mean(scores), 4)}
        for bucket, scores in sorted(grouped.items())
        if scores
    }


def _diagnostic_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}

    total_score = sum(float(row["score"]) for row in rows)
    total_tokens = sum(int(row.get("tokens_total", 0) or 0) for row in rows)
    total_latency = sum(float(row.get("latency_seconds", 0.0) or 0.0) for row in rows)
    total_prompt_tokens = sum(int(row.get("tokens_prompt", 0) or 0) for row in rows)
    total_cached_tokens = sum(int(row.get("tokens_cached", 0) or 0) for row in rows)
    cache_reported_cases = sum(
        1
        for row in rows
        if int(row.get("tokens_cached", 0) or 0)
        or int(row.get("cache_read_tokens", 0) or 0)
        or int(row.get("cache_creation_tokens", 0) or 0)
    )

    summary: dict[str, Any] = {
        "mean_session_count": _mean_field(rows, "session_count"),
        "mean_turn_count": _mean_field(rows, "turn_count"),
        "mean_trajectory_count": _mean_field(rows, "trajectory_count"),
        "mean_state_count": _mean_field(rows, "state_count"),
        "mean_available_chunks": _mean_field(rows, "available_chunks"),
        "mean_selected_chunks": _mean_field(rows, "selected_chunks"),
        "mean_available_context_tokens": _mean_field(
            rows,
            "available_context_tokens",
        ),
        "mean_selected_context_tokens": _mean_field(
            rows,
            "selected_context_tokens",
        ),
        "mean_context_compression_ratio": _mean_field(
            rows,
            "context_compression_ratio",
        ),
        "tokens_per_scored_point": (
            total_tokens / total_score if total_score > 0 else None
        ),
        "latency_seconds_per_scored_point": (
            total_latency / total_score if total_score > 0 else None
        ),
        "kv_cache": {
            "tokens_cached": total_cached_tokens,
            "cache_read_tokens": sum(
                int(row.get("cache_read_tokens", 0) or 0) for row in rows
            ),
            "cache_creation_tokens": sum(
                int(row.get("cache_creation_tokens", 0) or 0) for row in rows
            ),
            "reported_cases": cache_reported_cases,
            "prompt_token_hit_rate": (
                total_cached_tokens / total_prompt_tokens
                if total_prompt_tokens > 0
                else None
            ),
            "note": (
                "Zero cached tokens can mean either no KV-cache reuse or an "
                "OpenAI-compatible provider that does not expose cache usage."
            ),
        },
        "accuracy_by_context_tokens_bucket": _bucketed_accuracy(
            rows,
            "available_context_tokens",
            _bucket_context_tokens,
        ),
        "accuracy_by_turn_count_bucket": _bucketed_accuracy(
            rows,
            "turn_count",
            _bucket_count,
        ),
        "accuracy_by_trajectory_count_bucket": _bucketed_accuracy(
            rows,
            "trajectory_count",
            _bucket_count,
        ),
    }
    return {
        key: (round(value, 4) if isinstance(value, float) else value)
        for key, value in summary.items()
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
    per_case_diagnostics: list[dict[str, Any]] = []
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
        per_case_diagnostics.append(
            {
                "task_id": result.task_id,
                "score": score,
                "tokens_prompt": result.cost.tokens_prompt,
                "tokens_completion": result.cost.tokens_completion,
                "tokens_total": result.cost.tokens_total,
                "tokens_cached": result.cost.tokens_cached,
                "cache_creation_tokens": result.cost.cache_creation_tokens,
                "cache_read_tokens": result.cost.cache_read_tokens,
                "latency_seconds": result.cost.latency_seconds,
                "usd_cost": result.cost.usd_cost,
                **result.metadata,
            }
        )
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
            per_case_diagnostics=[],
            evidence_summary={},
            score_bounds=None,
            diagnostic_summary={},
        )

    mean_cost = CostLedger(
        tokens_prompt=total.tokens_prompt // len(all_scores),
        tokens_completion=total.tokens_completion // len(all_scores),
        tokens_cached=total.tokens_cached // len(all_scores),
        cache_creation_tokens=total.cache_creation_tokens // len(all_scores),
        cache_read_tokens=total.cache_read_tokens // len(all_scores),
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
        per_case_diagnostics=per_case_diagnostics,
        evidence_summary=dict(evidence_summary),
        score_bounds=score_bounds,
        diagnostic_summary=_diagnostic_summary(per_case_diagnostics),
    )
