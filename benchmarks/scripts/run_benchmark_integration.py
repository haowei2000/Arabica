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
class StepResult:
    name: str
    command: list[str]
    returncode: int
    elapsed_seconds: float


def _run_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> StepResult:
    """Run one command and capture timing/exit status."""
    start = time.perf_counter()
    returncode = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        check=False,
    ).returncode
    elapsed = time.perf_counter() - start
    return StepResult(
        name=" ".join(command),
        command=command,
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


def _build_steps(args: argparse.Namespace) -> list[list[str]]:
    """Build the benchmark command list based on selected modes."""
    python = sys.executable

    steps: list[list[str]] = []
    if not args.skip_unit:
        steps.append(
            [
                python,
                "-m",
                "pytest",
                "tests/benchmarks",
                "-m",
                "unit",
            ]
        )

    if not args.skip_fixture:
        steps.append(
            [
                python,
                "-m",
                "pytest",
                "tests/integration/test_open_source_benchmark_integration.py",
                "-m",
                "integration",
            ]
        )

    if not args.skip_baselines:
        steps.extend(
            [
                [
                    python,
                    "-m",
                    "benchmarks.scripts.run_memory_baselines",
                    "--benchmark",
                    "locomo",
                ],
                [
                    python,
                    "-m",
                    "benchmarks.scripts.run_memory_baselines",
                    "--benchmark",
                    "longmemeval",
                ],
            ]
        )

    if args.live and not args.skip_live:
        if not _has_live_credentials():
            raise SystemExit(
                "cannot run live benchmark mode: missing STRUCTURE_BENCHMARK_TOKEN "
                "or STRUCTURE_BENCHMARK_USERNAME/PASSWORD"
            )

        steps.append(
            [
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
            ]
        )
        steps.append(
            [
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
            ]
        )
    return steps


def _print_summary(results: Sequence[StepResult]) -> None:
    """Print concise step results."""
    for item in results:
        status = "PASS" if item.returncode == 0 else "FAIL"
        print(f"[{status}] {item.name} ({item.elapsed_seconds:.2f}s)")


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
    for command in steps:
        print(f"Running: {' '.join(command)}")
        results.append(_run_command(command, cwd=REPO_ROOT, env=os.environ.copy()))

    _print_summary(results)

    success = all(item.returncode == 0 for item in results)
    if not success:
        print("Benchmark workflow failed; check failed command output above.")
        return 1

    payload = {
        "status": "ok",
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
    if args.output:
        args.output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
