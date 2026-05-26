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

from benchmarks.adapters import (
    StructureMemoryBenchmarkAgent,
    StructurePathMemoryBenchmarkAgent,
)
from benchmarks.baselines import (
    LLMBenchmarkAgent,
    external_baseline_comparison,
)
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
    if method == "StructurePathMemory":
        return StructurePathMemoryBenchmarkAgent(
            data_root=output_dir / "structure-path-context",
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
    total_tokens = report.total_cost.tokens_total
    data.update(
        {
            "method": method,
            "reader_model": reader_model,
            "reader_base_url": reader_base_url,
            "judge": judge,
            "sample": sample_info,
            "external_baseline_comparison": external_baseline_comparison(
                benchmark=report.benchmark.split(":", maxsplit=1)[0],
                score=report.overall_score,
                latency_seconds=report.total_cost.latency_seconds,
                total_tokens=total_tokens,
            ),
            "metric_definitions": metric_definitions(),
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
            "per_case_diagnostics": report.per_case_diagnostics,
        }
    )
    return data


def metric_definitions() -> dict[str, str]:
    """Human-readable definitions for metrics emitted by this runner."""
    return {
        "accuracy": "Mean scorer output in [0, 1]; higher is better.",
        "sample": "Evaluated cases over source cases; sampled runs are not leaderboard claims.",
        "evidence": "pass means selected context or cited artifacts support the answer; unknown means support could not be verified.",
        "tokens_prompt": "Reader input tokens returned by the OpenAI-compatible API.",
        "tokens_completion": "Reader output tokens returned by the API.",
        "tokens_total": "Prompt plus completion tokens; lower is better at similar accuracy.",
        "tokens_cached": "Provider-reported cached prompt/KV tokens when exposed; zero may mean unreported.",
        "cache_creation_tokens": "Provider-reported tokens written into prompt/KV cache when exposed.",
        "cache_read_tokens": "Provider-reported prompt/KV cache-hit tokens when exposed.",
        "latency_seconds": "Wall-clock seconds for the model call path; lower is better at similar accuracy.",
        "tokens_per_scored_point": "Total tokens divided by sum of per-case scores; lower is better.",
        "latency_seconds_per_scored_point": "Total latency divided by sum of per-case scores; lower is better.",
        "turn_count": "Number of dialogue turns in session-style benchmark inputs.",
        "trajectory_count": "Number of LME-V2 trajectories attached to a case.",
        "state_count": "Known inline trajectory state count; lazy full-release LME-V2 cases may be unknown.",
        "available_chunks": "Memory chunks available before retrieval.",
        "selected_chunks": "Memory chunks selected for the reader prompt.",
        "available_context_tokens": "Approximate whitespace token count across all available chunks.",
        "selected_context_tokens": "Approximate whitespace token count across selected chunks.",
        "context_compression_ratio": "Selected context tokens divided by available context tokens; lower means stronger compression.",
        "external_baseline_comparison": "Source-linked paper/project reference rows for calibration, not proof of leaderboard comparability.",
    }


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
        "| Benchmark | Method | Sample | Accuracy | Evidence | Mean tokens | Total tokens | Sel/Avail chunks | Ctx compression | Tok/score | Lat/score | KV cached | Mean latency (s) | Cost |",
        "|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        evidence = row.get("evidence_summary", {})
        sample = row.get("sample", {})
        mean_cost = row["mean_cost"]
        total_cost = row["total_cost"]
        diagnostics = row.get("diagnostic_summary", {})
        selected_chunks = diagnostics.get("mean_selected_chunks")
        available_chunks = diagnostics.get("mean_available_chunks")
        kv_cache = diagnostics.get("kv_cache", {})
        sample_label = (
            f"{sample.get('sample_size')}/{sample.get('source_cases')} "
            f"({float(sample.get('sample_percent_effective', 0.0)):.2f}%)"
        )
        chunk_label = (
            f"{float(selected_chunks):.1f}/{float(available_chunks):.1f}"
            if selected_chunks is not None and available_chunks is not None
            else "-"
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
                    str(mean_cost["tokens_prompt"] + mean_cost["tokens_completion"]),
                    str(total_cost["tokens_prompt"] + total_cost["tokens_completion"]),
                    chunk_label,
                    _format_optional_float(
                        diagnostics.get("mean_context_compression_ratio"),
                        digits=3,
                    ),
                    _format_optional_float(
                        diagnostics.get("tokens_per_scored_point"),
                        digits=1,
                    ),
                    _format_optional_float(
                        diagnostics.get("latency_seconds_per_scored_point"),
                        digits=3,
                    ),
                    str(kv_cache.get("tokens_cached", 0)),
                    f"{float(mean_cost['latency_seconds']):.3f}",
                    f"{float(total_cost['usd_cost']):.6f}",
                ]
            )
            + " |"
        )

    external_rows: list[dict[str, object]] = []
    seen: set[tuple[str, str, float]] = set()
    for row in rows:
        benchmark = str(row["benchmark"]).split(":", maxsplit=1)[0]
        for baseline in row.get("external_baseline_comparison", []):
            key = (benchmark, str(baseline["method"]), float(baseline["score"]))
            if key in seen:
                continue
            seen.add(key)
            external_rows.append({"benchmark": benchmark, **baseline})
    if external_rows:
        lines.extend(
            [
                "",
                "External paper/project baselines (calibration only):",
                "",
                "| Benchmark | External method | Metric | Score | Local delta | Source | Notes |",
                "|---|---|---|---:|---:|---|---|",
            ]
        )
        for row in external_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(row["benchmark"]),
                        str(row["method"]),
                        str(row["metric_name"]),
                        f"{float(row['score']):.4f}",
                        _format_optional_float(row.get("score_delta_vs_local")),
                        f"[{row['source_title']}]({row['source_url']})",
                        str(row.get("comparability", "")),
                    ]
                )
                + " |"
            )
    return "\n".join(lines)


def _format_optional_float(value: object, *, digits: int = 4) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{float(value):.{digits}f}"
    return str(value)


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
        choices=("FullText", "NaiveRAG", "StructureMemory", "StructurePathMemory"),
        default=["FullText", "NaiveRAG", "StructureMemory"],
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
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
    api_key, base_url, model = _require_openai_env()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    reports, sample_info = asyncio.run(
        run_suite(
            benchmark=args.benchmark,
            data_dir=args.data_dir,
            methods=args.methods,
            api_key=api_key,
            base_url=base_url,
            model=model,
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
            reader_model=model,
            reader_base_url=base_url,
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
