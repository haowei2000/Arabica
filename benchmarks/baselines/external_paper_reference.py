"""External paper and project baselines for benchmark report calibration.

The values here are not Structure measurements. They are source-linked
reference rows that make local sampled reports easier to interpret without
mixing them into leaderboard claims.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass

from benchmarks.baselines.lightmem_reference import iter_locomo_overview

LONGMEMEVAL_V2_PROJECT_URL = "https://xiaowu0162.github.io/longmemeval-v2/"
LIGHTMEM_SOURCE_TITLE = "LightMem / MemBase LoCoMo reported baselines"
LONGMEMEVAL_V2_SOURCE_TITLE = "LongMemEval-V2 project leaderboard"


@dataclass(frozen=True)
class ExternalBenchmarkBaseline:
    """A source-linked external baseline row."""

    benchmark: str
    method: str
    score: float
    metric_name: str
    source_title: str
    source_url: str
    latency_seconds: float | None = None
    total_tokens: int | None = None
    notes: str = ""
    comparability: str = "calibration-only"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


LONGMEMEVAL_V2_BASELINES: tuple[ExternalBenchmarkBaseline, ...] = (
    ExternalBenchmarkBaseline(
        benchmark="longmemeval-v2-small",
        method="AgentRunbook-C",
        score=0.749,
        metric_name="accuracy",
        source_title=LONGMEMEVAL_V2_SOURCE_TITLE,
        source_url=LONGMEMEVAL_V2_PROJECT_URL,
        notes=(
            "Project-page Small split result. Not directly comparable with "
            "sampled local text-only runs unless reader, judge, evidence, and "
            "multimodal inputs match the official protocol."
        ),
    ),
    ExternalBenchmarkBaseline(
        benchmark="longmemeval-v2-medium",
        method="AgentRunbook-C",
        score=0.701,
        metric_name="accuracy",
        source_title=LONGMEMEVAL_V2_SOURCE_TITLE,
        source_url=LONGMEMEVAL_V2_PROJECT_URL,
        notes=(
            "Project-page Medium split result. Not directly comparable with "
            "sampled local text-only runs unless reader, judge, evidence, and "
            "multimodal inputs match the official protocol."
        ),
    ),
)


def iter_external_baselines(
    *,
    benchmark: str | None = None,
) -> Iterable[ExternalBenchmarkBaseline]:
    """Yield source-linked external baseline rows, optionally filtered."""
    for row in iter_locomo_overview(backbone="gpt-4o-mini"):
        baseline = ExternalBenchmarkBaseline(
            benchmark="locomo",
            method=row.method,
            score=row.accuracy_gpt4o_judge / 100,
            metric_name="GPT-4o judge accuracy",
            source_title=LIGHTMEM_SOURCE_TITLE,
            source_url=row.source_url,
            latency_seconds=float(row.runtime_seconds),
            total_tokens=int(row.total_tokens_k * 1000),
            notes=(
                "Reported LoCoMo full-run baseline with a GPT-4o-mini backbone. "
                "Use for calibration only because local runs may use a different "
                "reader, scorer, judge, and sample."
            ),
        )
        if benchmark is None or benchmark == baseline.benchmark:
            yield baseline

    for baseline in LONGMEMEVAL_V2_BASELINES:
        if benchmark is None or benchmark == baseline.benchmark:
            yield baseline


def external_baseline_comparison(
    *,
    benchmark: str,
    score: float,
    latency_seconds: float | None,
    total_tokens: int | None,
) -> list[dict[str, object]]:
    """Compare one local row against matching external reference rows."""
    rows: list[dict[str, object]] = []
    for baseline in iter_external_baselines(benchmark=benchmark):
        payload = baseline.to_dict()
        payload["score_delta_vs_local"] = round(score - baseline.score, 4)
        if latency_seconds is not None and baseline.latency_seconds is not None:
            payload["latency_delta_seconds_vs_local"] = round(
                latency_seconds - baseline.latency_seconds,
                4,
            )
        if total_tokens is not None and baseline.total_tokens is not None:
            payload["token_delta_vs_local"] = int(total_tokens - baseline.total_tokens)
        rows.append(payload)
    return rows
