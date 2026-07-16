"""Run lightweight memory baselines on local benchmark fixtures.

This is a CI-friendly smoke runner. It exercises FullText and NaiveRAG
context selection without calling an LLM, so results are only harness
checks and should not be reported as model performance.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
import json
from pathlib import Path

from benchmarks.baselines import make_memory_baseline
from benchmarks.core import BenchmarkCase, BenchmarkReport, BenchmarkRunner
from benchmarks.locomo import load_locomo, locomo_qa_scorer
from benchmarks.longmemeval import load_longmemeval, longmemeval_scorer

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASETS = {
    "locomo": REPO_ROOT / "benchmarks" / "locomo" / "fixtures" / "sample.json",
    "longmemeval": REPO_ROOT
    / "benchmarks"
    / "longmemeval"
    / "fixtures"
    / "sample.json",
}

Loader = Callable[[str | Path], list[BenchmarkCase]]
Scorer = Callable[[object, object], float]


def _benchmark_config(name: str) -> tuple[Loader, Scorer]:
    if name == "locomo":
        return load_locomo, locomo_qa_scorer
    if name == "longmemeval":
        return load_longmemeval, longmemeval_scorer
    raise ValueError(f"unsupported benchmark: {name}")


async def run_suite(
    *,
    benchmark: str,
    dataset: Path,
    baseline_names: list[str],
    top_k: int,
) -> list[BenchmarkReport]:
    loader, scorer = _benchmark_config(benchmark)
    cases = loader(dataset)
    reports: list[BenchmarkReport] = []
    for baseline_name in baseline_names:
        runner = BenchmarkRunner(
            benchmark_name=f"{benchmark}:{baseline_name}",
            agent=make_memory_baseline(baseline_name, top_k=top_k),
            scorer=scorer,
        )
        reports.append(await runner.run(cases))
    return reports


def _report_dict(report: BenchmarkReport) -> dict[str, object]:
    data = report.to_dict()
    data["per_case"] = [
        {
            "task_id": task_id,
            "score": round(score, 4),
            "tokens_total": cost.tokens_total,
        }
        for task_id, score, cost in report.per_case
    ]
    return data


def render_json(reports: list[BenchmarkReport]) -> str:
    return json.dumps([_report_dict(report) for report in reports], indent=2)


def render_markdown(reports: list[BenchmarkReport]) -> str:
    lines = [
        "| Benchmark | Cases | Score | Mean tokens | Total tokens |",
        "|---|---:|---:|---:|---:|",
    ]
    for report in reports:
        lines.append(
            "| "
            + " | ".join(
                [
                    report.benchmark,
                    str(report.n_cases),
                    f"{report.overall_score:.4f}",
                    str(report.mean_cost.tokens_total),
                    str(report.total_cost.tokens_total),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run deterministic FullText/NaiveRAG memory baseline smoke tests.",
    )
    parser.add_argument(
        "--benchmark",
        choices=tuple(DEFAULT_DATASETS),
        default="locomo",
        help="Benchmark adapter to run.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        help="Dataset path. Defaults to the bundled fixture for the benchmark.",
    )
    parser.add_argument(
        "--baselines",
        nargs="+",
        default=["FullText", "NaiveRAG"],
        help="Memory baselines to run.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Number of chunks used by NaiveRAG.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write output to a file instead of stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dataset = args.dataset or DEFAULT_DATASETS[args.benchmark]
    reports = asyncio.run(
        run_suite(
            benchmark=args.benchmark,
            dataset=dataset,
            baseline_names=args.baselines,
            top_k=args.top_k,
        )
    )

    rendered = (
        render_json(reports) if args.format == "json" else render_markdown(reports)
    )
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
