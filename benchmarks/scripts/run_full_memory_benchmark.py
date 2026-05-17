"""Run full memory benchmarks with fixed reader and deterministic judge."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Sequence
import hashlib
import json
import math
import os
from pathlib import Path
import random

from benchmarks.adapters import StructureMemoryBenchmarkAgent
from benchmarks.baselines import DEFAULT_BASE_URL, DEFAULT_MODEL, LLMBenchmarkAgent
from benchmarks.core import BenchmarkCase, BenchmarkReport, BenchmarkRunner
from benchmarks.locomo import load_locomo, locomo_qa_scorer
from benchmarks.longmemeval import load_longmemeval, longmemeval_scorer
from benchmarks.longmemeval_v2 import (
    load_longmemeval_v2_release,
    longmemeval_v2_scorer,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "benchmarks" / "data"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "benchmark_runs"

Loader = Callable[[Path], list[BenchmarkCase]]
Scorer = Callable[[object, object], float]


def _benchmark_config(name: str, data_dir: Path) -> tuple[Path, Loader, Scorer]:
    if name == "locomo":
        return data_dir / "locomo" / "locomo10.json", load_locomo, locomo_qa_scorer
    if name == "longmemeval-s":
        return (
            data_dir / "longmemeval" / "longmemeval_s_cleaned.json",
            load_longmemeval,
            longmemeval_scorer,
        )
    if name == "longmemeval-m":
        return (
            data_dir / "longmemeval" / "longmemeval_m_cleaned.json",
            load_longmemeval,
            longmemeval_scorer,
        )
    if name == "longmemeval-v2-small":
        return (
            data_dir / "longmemeval_v2",
            lambda path: load_longmemeval_v2_release(path, split="small"),
            longmemeval_v2_scorer,
        )
    if name == "longmemeval-v2-medium":
        return (
            data_dir / "longmemeval_v2",
            lambda path: load_longmemeval_v2_release(path, split="medium"),
            longmemeval_v2_scorer,
        )
    raise ValueError(f"unsupported benchmark: {name}")


def _make_agent(
    *,
    method: str,
    api_key: str,
    base_url: str,
    model: str,
    top_k: int,
    temperature: float,
    max_context_chars: int,
    input_cost_per_mtok: float,
    output_cost_per_mtok: float,
    output_dir: Path,
):
    common = {
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "top_k": top_k,
        "temperature": temperature,
        "max_context_chars": max_context_chars,
        "input_cost_per_mtok": input_cost_per_mtok,
        "output_cost_per_mtok": output_cost_per_mtok,
    }
    if method == "FullText":
        return LLMBenchmarkAgent(context_mode="fulltext", **common)
    if method == "NaiveRAG":
        return LLMBenchmarkAgent(context_mode="naiverag", **common)
    if method == "StructureMemory":
        return StructureMemoryBenchmarkAgent(
            data_root=output_dir / "structure-context",
            **common,
        )
    raise ValueError(f"unsupported method: {method}")


def sample_cases(
    cases: Sequence[BenchmarkCase],
    *,
    sample_mode: str = "prefix",
    sample_seed: str = "structure-memory-benchmark-v1",
    max_cases: int | None = None,
    sample_percent: float | None = None,
) -> tuple[list[BenchmarkCase], dict[str, object]]:
    """Apply deterministic benchmark sampling.

    ``prefix`` preserves the legacy behavior, ``hash`` gives a stable fixed
    sample independent of dataset order changes, and ``random`` gives a seeded
    pseudo-random sample.  Both non-prefix modes preserve dataset order after
    selecting cases so output diffs remain easy to read.
    """
    total = len(cases)
    if sample_percent is not None and not 0 < sample_percent <= 100:
        raise ValueError("--sample-percent must be in (0, 100]")
    if max_cases is not None and max_cases < 1:
        raise ValueError("--max-cases must be positive")

    if sample_percent is None:
        target = total if max_cases is None else min(max_cases, total)
    else:
        target = min(max(1, math.ceil(total * sample_percent / 100)), total)
        if max_cases is not None:
            target = min(target, max_cases)

    if target >= total:
        sampled = list(cases)
    elif sample_mode == "prefix":
        sampled = list(cases[:target])
    elif sample_mode == "random":
        rng = random.Random(f"{sample_seed}:{total}:{target}")
        indices = sorted(rng.sample(range(total), target))
        sampled = [cases[index] for index in indices]
    elif sample_mode == "hash":
        ranked = sorted(
            enumerate(cases),
            key=lambda item: hashlib.sha256(
                f"{sample_seed}:{item[1].task_id}".encode()
            ).hexdigest(),
        )
        indices = sorted(index for index, _ in ranked[:target])
        sampled = [cases[index] for index in indices]
    else:
        raise ValueError(f"unsupported sample mode: {sample_mode}")

    effective_percent = (len(sampled) / total * 100) if total else 0.0
    return sampled, {
        "sample_mode": sample_mode,
        "sample_seed": sample_seed,
        "sample_percent_requested": sample_percent,
        "sample_percent_effective": effective_percent,
        "sample_size": len(sampled),
        "source_cases": total,
        "max_cases": max_cases,
    }


def _report_dict(
    report: BenchmarkReport,
    *,
    method: str,
    reader_model: str,
    reader_base_url: str,
    judge: str,
    sample_info: dict[str, object],
) -> dict[str, object]:
    data = report.to_dict()
    data.update(
        {
            "method": method,
            "reader_model": reader_model,
            "reader_base_url": reader_base_url,
            "judge": judge,
            "sample": sample_info,
            "per_case": [
                {
                    "task_id": task_id,
                    "score": round(score, 4),
                    "tokens_prompt": cost.tokens_prompt,
                    "tokens_completion": cost.tokens_completion,
                    "tokens_total": cost.tokens_total,
                    "latency_seconds": round(cost.latency_seconds, 3),
                    "usd_cost": round(cost.usd_cost, 8),
                }
                for task_id, score, cost in report.per_case
            ],
        }
    )
    return data


async def run_suite(
    *,
    benchmark: str,
    data_dir: Path,
    methods: Sequence[str],
    api_key: str,
    base_url: str,
    model: str,
    top_k: int,
    temperature: float,
    max_cases: int | None,
    sample_mode: str,
    sample_seed: str,
    sample_percent: float | None,
    max_context_chars: int,
    input_cost_per_mtok: float,
    output_cost_per_mtok: float,
    output_dir: Path,
) -> tuple[list[tuple[str, BenchmarkReport]], dict[str, object]]:
    dataset_path, loader, scorer = _benchmark_config(benchmark, data_dir)
    if not dataset_path.exists():
        raise FileNotFoundError(
            f"{dataset_path} does not exist. Run prepare_full_datasets.py first."
        )

    cases, sample_info = sample_cases(
        loader(dataset_path),
        sample_mode=sample_mode,
        sample_seed=sample_seed,
        max_cases=max_cases,
        sample_percent=sample_percent,
    )

    reports: list[tuple[str, BenchmarkReport]] = []
    for method in methods:
        case_output = output_dir / f"{benchmark}-{method}-cases.jsonl"
        case_output.parent.mkdir(parents=True, exist_ok=True)
        case_output.write_text("", encoding="utf-8")

        def on_case(case: BenchmarkCase, result, *, path=case_output) -> None:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "task_id": case.task_id,
                            "ability": case.ability,
                            "response": result.response,
                            "cost": result.cost.__dict__,
                            "evidence": result.evidence.__dict__
                            if result.evidence
                            else None,
                            "metadata": result.metadata,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        agent = _make_agent(
            method=method,
            api_key=api_key,
            base_url=base_url,
            model=model,
            top_k=top_k,
            temperature=temperature,
            max_context_chars=max_context_chars,
            input_cost_per_mtok=input_cost_per_mtok,
            output_cost_per_mtok=output_cost_per_mtok,
            output_dir=output_dir,
        )
        runner = BenchmarkRunner(
            benchmark_name=f"{benchmark}:{method}:{model}",
            agent=agent,
            scorer=scorer,
            on_case=on_case,
        )
        reports.append((method, await runner.run(cases)))
    return reports, sample_info


def render_markdown(rows: list[dict[str, object]]) -> str:
    lines = [
        "| Benchmark | Method | Sample | Accuracy | Evidence | Mean tokens | Total tokens | Mean latency (s) | Cost |",
        "|---|---|---:|---:|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        evidence = row.get("evidence_summary", {})
        sample = row.get("sample", {})
        sample_label = (
            f"{sample.get('sample_size')}/{sample.get('source_cases')} "
            f"({float(sample.get('sample_percent_effective', 0.0)):.2f}%)"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["benchmark"]),
                    str(row["method"]),
                    sample_label,
                    f"{float(row['overall_score']):.4f}",
                    json.dumps(evidence, sort_keys=True),
                    str(row["mean_cost"]["tokens_total"]),
                    str(row["total_cost"]["tokens_total"]),
                    f"{float(row['mean_cost']['latency_seconds']):.3f}",
                    f"{float(row['total_cost']['usd_cost']):.6f}",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run full memory benchmarks with fixed reader and judge.",
    )
    parser.add_argument(
        "--benchmark",
        choices=(
            "locomo",
            "longmemeval-s",
            "longmemeval-m",
            "longmemeval-v2-small",
            "longmemeval-v2-medium",
        ),
        required=True,
    )
    parser.add_argument(
        "--methods",
        nargs="+",
        choices=("FullText", "NaiveRAG", "StructureMemory"),
        default=["FullText", "NaiveRAG", "StructureMemory"],
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--api-key-env", default="BENCHMARK_LLM_API_KEY")
    parser.add_argument(
        "--base-url",
        default=os.getenv("BENCHMARK_LLM_BASE_URL", DEFAULT_BASE_URL),
    )
    parser.add_argument(
        "--model", default=os.getenv("BENCHMARK_LLM_MODEL", DEFAULT_MODEL)
    )
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument(
        "--sample-mode",
        choices=("prefix", "hash", "random"),
        default="prefix",
    )
    parser.add_argument(
        "--sample-seed",
        default="structure-memory-benchmark-v1",
        help="Stable seed used by hash/random sampling.",
    )
    parser.add_argument(
        "--sample-percent",
        type=float,
        help="Evaluate this percentage of the benchmark before applying --max-cases cap.",
    )
    parser.add_argument("--max-context-chars", type=int, default=120_000)
    parser.add_argument("--input-cost-per-mtok", type=float, default=0.0)
    parser.add_argument("--output-cost-per-mtok", type=float, default=0.0)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    api_key = os.getenv(args.api_key_env, "")
    if not api_key:
        raise SystemExit(f"missing API key: export {args.api_key_env}=...")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    reports, sample_info = asyncio.run(
        run_suite(
            benchmark=args.benchmark,
            data_dir=args.data_dir,
            methods=args.methods,
            api_key=api_key,
            base_url=args.base_url,
            model=args.model,
            top_k=args.top_k,
            temperature=args.temperature,
            max_cases=args.max_cases,
            sample_mode=args.sample_mode,
            sample_seed=args.sample_seed,
            sample_percent=args.sample_percent,
            max_context_chars=args.max_context_chars,
            input_cost_per_mtok=args.input_cost_per_mtok,
            output_cost_per_mtok=args.output_cost_per_mtok,
            output_dir=args.output_dir,
        )
    )

    rows = [
        _report_dict(
            report,
            method=method,
            reader_model=args.model,
            reader_base_url=args.base_url,
            judge="benchmark-specific-deterministic-scorer",
            sample_info=sample_info,
        )
        for method, report in reports
    ]
    rendered = (
        json.dumps(rows, indent=2, ensure_ascii=False)
        if args.format == "json"
        else render_markdown(rows)
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
