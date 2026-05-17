"""Tests for the runnable memory baseline script."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).parents[2]


@pytest.mark.unit
def test_memory_baseline_runner_outputs_locomo_json():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.scripts.run_memory_baselines",
            "--benchmark",
            "locomo",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        cwd=REPO_ROOT,
        text=True,
    )
    rows = json.loads(completed.stdout)
    assert [row["benchmark"] for row in rows] == ["locomo:FullText", "locomo:NaiveRAG"]
    assert all(row["overall_score"] == 1.0 for row in rows)
    assert all(row["n_cases"] == 3 for row in rows)


@pytest.mark.unit
def test_memory_baseline_runner_outputs_longmemeval_markdown():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.scripts.run_memory_baselines",
            "--benchmark",
            "longmemeval",
            "--format",
            "markdown",
        ],
        check=True,
        capture_output=True,
        cwd=REPO_ROOT,
        text=True,
    )
    assert "| longmemeval:FullText |" in completed.stdout
    assert "| longmemeval:NaiveRAG |" in completed.stdout
