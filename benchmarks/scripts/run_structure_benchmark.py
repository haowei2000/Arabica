"""Run benchmark fixtures or datasets through a live Structure service."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
import json
import os
from pathlib import Path

from benchmarks.adapters import StructureRunBenchmarkAgent
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


async def run_structure_suite(
    *,
    benchmark: str,
    dataset: Path,
    base_url: str,
    token: str | None,
    username: str | None,
    password: str | None,
    workspace_id: str | None,
    app_id: str | None,
    executor_code: str | None,
    timeout_seconds: float,
    poll_interval_seconds: float,
    max_cases: int | None,
    cleanup_workspaces: bool,
    use_sse: bool,
) -> BenchmarkReport:
    loader, scorer = _benchmark_config(benchmark)
    cases = loader(dataset)
    if max_cases is not None:
        cases = cases[:max_cases]

    agent = StructureRunBenchmarkAgent(
        base_url=base_url,
        token=token,
        username=username,
        password=password,
        workspace_id=workspace_id,
        app_id=app_id,
        executor_code=executor_code,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        cleanup_workspaces=cleanup_workspaces,
        use_sse=use_sse,
    )
    try:
        runner = BenchmarkRunner(
            benchmark_name=f"{benchmark}:structure-run",
            agent=agent,
            scorer=scorer,
        )
        return await runner.run(cases)
    finally:
        await agent.aclose()


def _report_dict(report: BenchmarkReport) -> dict[str, object]:
    data = report.to_dict()
    data["per_case"] = [
        {
            "task_id": task_id,
            "score": round(score, 4),
            "tokens_prompt": cost.tokens_prompt,
            "tokens_completion": cost.tokens_completion,
            "tokens_total": cost.tokens_total,
            "tool_calls": cost.tool_calls,
            "steps": cost.steps,
            "latency_seconds": round(cost.latency_seconds, 3),
        }
        for task_id, score, cost in report.per_case
    ]
    data["per_case_diagnostics"] = report.per_case_diagnostics
    return data


def render_json(report: BenchmarkReport) -> str:
    return json.dumps(_report_dict(report), indent=2, ensure_ascii=False)


def render_markdown(report: BenchmarkReport) -> str:
    lines = [
        "| Benchmark | Cases | Score | Mean tokens | Total tokens | Tool calls | Mean latency (s) |",
        "|---|---:|---:|---:|---:|---:|---:|",
        "| "
        + " | ".join(
            [
                report.benchmark,
                str(report.n_cases),
                f"{report.overall_score:.4f}",
                str(report.mean_cost.tokens_total),
                str(report.total_cost.tokens_total),
                str(report.total_cost.tool_calls),
                f"{report.mean_cost.latency_seconds:.3f}",
            ]
        )
        + " |",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run benchmark cases through a local Structure service.",
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
        "--base-url",
        default=os.getenv("STRUCTURE_BENCHMARK_BASE_URL", "http://127.0.0.1:8000"),
        help="Structure service base URL. May include or omit /api.",
    )
    parser.add_argument(
        "--token-env",
        default="STRUCTURE_BENCHMARK_TOKEN",
        help="Environment variable containing a bearer token.",
    )
    parser.add_argument(
        "--username-env",
        default="STRUCTURE_BENCHMARK_USERNAME",
        help="Environment variable containing the login username.",
    )
    parser.add_argument(
        "--password-env",
        default="STRUCTURE_BENCHMARK_PASSWORD",
        help="Environment variable containing the login password.",
    )
    parser.add_argument("--workspace-id", help="Reuse an existing workspace.")
    parser.add_argument("--app-id", help="Optional app ID for runs.")
    parser.add_argument(
        "--executor-code",
        default="SimpleAgent",
        help="Executor code used for created workspaces.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=300.0,
        help="Maximum wait per case.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=1.0,
        help="Polling interval when SSE is unavailable.",
    )
    parser.add_argument("--max-cases", type=int, help="Limit evaluated cases.")
    parser.add_argument(
        "--cleanup-workspaces",
        action="store_true",
        help="Delete adapter-created workspaces after each case.",
    )
    parser.add_argument(
        "--no-sse",
        action="store_true",
        help="Skip SSE waiting and use polling only.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format.",
    )
    parser.add_argument("--output", type=Path, help="Write output to a file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.getenv(args.token_env) or None
    username = os.getenv(args.username_env) or None
    password = os.getenv(args.password_env) or None

    if not token and not (username and password):
        raise SystemExit(
            "missing Structure credentials: export a bearer token or username/password"
        )

    dataset = args.dataset or DEFAULT_DATASETS[args.benchmark]
    report = asyncio.run(
        run_structure_suite(
            benchmark=args.benchmark,
            dataset=dataset,
            base_url=args.base_url,
            token=token,
            username=username,
            password=password,
            workspace_id=args.workspace_id,
            app_id=args.app_id,
            executor_code=args.executor_code,
            timeout_seconds=args.timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            max_cases=args.max_cases,
            cleanup_workspaces=args.cleanup_workspaces,
            use_sse=not args.no_sse,
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
