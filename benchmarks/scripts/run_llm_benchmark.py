"""Run LongMemEval/LoCoMo with a real OpenAI-compatible chat model."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
import json
import os
from pathlib import Path

from benchmarks.baselines import LLMBenchmarkAgent
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


async def run_llm_suite(
    *,
    benchmark: str,
    dataset: Path,
    api_key: str,
    base_url: str,
    model: str,
    context_mode: str,
    top_k: int,
    temperature: float,
    max_cases: int | None,
) -> BenchmarkReport:
    loader, scorer = _benchmark_config(benchmark)
    cases = loader(dataset)
    if max_cases is not None:
        cases = cases[:max_cases]

    agent = LLMBenchmarkAgent(
        model=model,
        api_key=api_key,
        base_url=base_url,
        context_mode=context_mode,
        top_k=top_k,
        temperature=temperature,
    )
    runner = BenchmarkRunner(
        benchmark_name=f"{benchmark}:llm-{context_mode}:{model}",
        agent=agent,
        scorer=scorer,
    )
    return await runner.run(cases)


def _report_dict(report: BenchmarkReport) -> dict[str, object]:
    data = report.to_dict()
    data["per_case"] = [
        {
            "task_id": task_id,
            "score": round(score, 4),
            "tokens_prompt": cost.tokens_prompt,
            "tokens_completion": cost.tokens_completion,
            "tokens_total": cost.tokens_total,
            "latency_seconds": round(cost.latency_seconds, 3),
        }
        for task_id, score, cost in report.per_case
    ]
    return data


def render_json(report: BenchmarkReport) -> str:
    return json.dumps(_report_dict(report), indent=2, ensure_ascii=False)


def render_markdown(report: BenchmarkReport) -> str:
    lines = [
        "| Benchmark | Cases | Score | Mean tokens | Total tokens | Mean latency (s) |",
        "|---|---:|---:|---:|---:|---:|",
        "| "
        + " | ".join(
            [
                report.benchmark,
                str(report.n_cases),
                f"{report.overall_score:.4f}",
                str(report.mean_cost.tokens_total),
                str(report.total_cost.tokens_total),
                f"{report.mean_cost.latency_seconds:.3f}",
            ]
        )
        + " |",
    ]
    return "\n".join(lines)


def _require_openai_env() -> tuple[str, str, str]:
    api_key = os.getenv("OPENAI__API_KEY", "").strip()
    base_url = os.getenv("OPENAI__BASE_URL", "").strip()
    model = os.getenv("OPENAI__MODEL", "").strip()
    missing = [
        name
        for name, value in (
            ("OPENAI__API_KEY", api_key),
            ("OPENAI__BASE_URL", base_url),
            ("OPENAI__MODEL", model),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            "missing LLM environment variables: export "
            + ", ".join(f"{name}=..." for name in missing)
        )
    return api_key, base_url, model


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run benchmark fixtures or datasets with a real chat model.",
    )
    parser.add_argument(
        "--benchmark",
        choices=tuple(DEFAULT_DATASETS),
        default="longmemeval",
        help="Benchmark adapter to run.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        help="Dataset path. Defaults to the bundled fixture for the benchmark.",
    )
    parser.add_argument(
        "--context-mode",
        choices=("fulltext", "naiverag"),
        default="fulltext",
        help="How sessions are loaded into the prompt.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=6,
        help="Number of chunks used when --context-mode=naiverag.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="LLM sampling temperature.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        help="Limit the number of cases for live smoke tests.",
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
    api_key, base_url, model = _require_openai_env()

    dataset = args.dataset or DEFAULT_DATASETS[args.benchmark]
    report = asyncio.run(
        run_llm_suite(
            benchmark=args.benchmark,
            dataset=dataset,
            api_key=api_key,
            base_url=base_url,
            model=model,
            context_mode=args.context_mode,
            top_k=args.top_k,
            temperature=args.temperature,
            max_cases=args.max_cases,
        )
    )

    rendered = render_json(report) if args.format == "json" else render_markdown(report)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
