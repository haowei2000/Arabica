"""Run a consolidated benchmark integration workflow.

This command groups offline benchmark checks into a single flow so
developers can run the same command locally and in CI prep scripts.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAX_CASES = 4


@dataclass
class StepSpec:
    name: str
    command: list[str]


@dataclass
class StepResult:
    name: str
    command: list[str]
    returncode: int
    elapsed_seconds: float


def _run_command(step: StepSpec, *, cwd: Path, env: dict[str, str]) -> StepResult:
    """Run one command and capture timing/exit status."""
    start = time.perf_counter()
    returncode = subprocess.run(
        step.command,
        cwd=str(cwd),
        env=env,
        check=False,
    ).returncode
    elapsed = time.perf_counter() - start
    return StepResult(
        name=step.name,
        command=step.command,
        returncode=returncode,
        elapsed_seconds=elapsed,
    )


def _has_live_credentials() -> bool:
    """Return whether live Structure credentials are available."""
    env = os.environ
    token = env.get("STRUCTURE_BENCHMARK_TOKEN", "").strip()
    username = env.get("STRUCTURE_BENCHMARK_USERNAME", "").strip()
    password = env.get("STRUCTURE_BENCHMARK_PASSWORD", "").strip()
    return bool(token or (username and password))


def _build_steps(args: argparse.Namespace) -> list[StepSpec]:
    """Build the benchmark command list based on selected modes."""
    python = sys.executable

    steps: list[StepSpec] = []
    if not args.skip_unit:
        steps.append(
            StepSpec(
                name="benchmark-unit",
                command=[
                    python,
                    "-m",
                    "pytest",
                    "tests/benchmarks",
                    "-m",
                    "unit",
                ],
            )
        )

    if not args.skip_fixture:
        steps.append(
            StepSpec(
                name="open-source-fixtures",
                command=[
                    python,
                    "-m",
                    "pytest",
                    "tests/integration/test_open_source_benchmark_integration.py",
                    "-m",
                    "integration",
                    "--run-integration",
                ],
            )
        )

    if not args.skip_baselines:
        steps.extend(
            [
                StepSpec(
                    name="memory-baseline-locomo",
                    command=[
                        python,
                        "-m",
                        "benchmarks.scripts.run_memory_baselines",
                        "--benchmark",
                        "locomo",
                    ],
                ),
                StepSpec(
                    name="memory-baseline-longmemeval",
                    command=[
                        python,
                        "-m",
                        "benchmarks.scripts.run_memory_baselines",
                        "--benchmark",
                        "longmemeval",
                    ],
                ),
            ]
        )

    if args.live and not args.skip_live:
        if not _has_live_credentials():
            raise SystemExit(
                "cannot run live benchmark mode: missing STRUCTURE_BENCHMARK_TOKEN "
                "or STRUCTURE_BENCHMARK_USERNAME/PASSWORD"
            )

        steps.append(
            StepSpec(
                name="live-structure-longmemeval",
                command=[
                    python,
                    "-m",
                    "benchmarks.scripts.run_structure_benchmark",
                    "--benchmark",
                    "longmemeval",
                    "--max-cases",
                    str(args.max_cases),
                    "--format",
                    args.format,
                    "--cleanup-workspaces",
                ],
            )
        )
        steps.append(
            StepSpec(
                name="live-structure-locomo",
                command=[
                    python,
                    "-m",
                    "benchmarks.scripts.run_structure_benchmark",
                    "--benchmark",
                    "locomo",
                    "--max-cases",
                    str(args.max_cases),
                    "--format",
                    args.format,
                    "--cleanup-workspaces",
                ],
            )
        )
    return steps


def _print_summary(results: Sequence[StepResult]) -> None:
    """Print concise step results."""
    for item in results:
        status = "PASS" if item.returncode == 0 else "FAIL"
        print(f"[{status}] {item.name} ({item.elapsed_seconds:.2f}s)")


def _summary_payload(results: Sequence[StepResult]) -> dict[str, object]:
    """Build a machine-readable report for successful and failed workflows."""
    failed_steps = [item.name for item in results if item.returncode != 0]
    return {
        "status": "ok" if not failed_steps else "failed",
        "failed_steps": failed_steps,
        "results": [
            {
                "name": item.name,
                "command": item.command,
                "returncode": item.returncode,
                "elapsed_seconds": round(item.elapsed_seconds, 3),
            }
            for item in results
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run local benchmark integration workflow.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=DEFAULT_MAX_CASES,
        help="Max cases for live Structure runs.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="Output format for live runs.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write JSON summary to a file.",
    )
    parser.add_argument(
        "--skip-unit",
        action="store_true",
        help="Skip benchmark unit tests.",
    )
    parser.add_argument(
        "--skip-fixture",
        action="store_true",
        help="Skip open-source fixture integration tests.",
    )
    parser.add_argument(
        "--skip-baselines",
        action="store_true",
        help="Skip deterministic memory-baseline smoke commands.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Include live Structure benchmark smoke runs.",
    )
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Skip live Structure runs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    steps = _build_steps(args)

    results: list[StepResult] = []
    for step in steps:
        print(f"Running {step.name}: {' '.join(step.command)}", flush=True)
        results.append(_run_command(step, cwd=REPO_ROOT, env=os.environ.copy()))

    _print_summary(results)

    success = all(item.returncode == 0 for item in results)
    payload = _summary_payload(results)
    rendered_payload = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered_payload, encoding="utf-8")
    else:
        print(rendered_payload)

    if not success:
        print("Benchmark workflow failed; check failed command output above.")
        return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
