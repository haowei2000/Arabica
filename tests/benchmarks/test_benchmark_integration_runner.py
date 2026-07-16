"""Tests for the consolidated benchmark integration runner."""

import json

from benchmarks.scripts import run_benchmark_integration as runner
import pytest


@pytest.mark.unit
def test_default_benchmark_integration_steps_are_offline_only():
    args = runner.build_parser().parse_args([])

    steps = runner._build_steps(args)
    step_names = [step.name for step in steps]
    joined_steps = [" ".join(step.command) for step in steps]

    assert step_names == [
        "benchmark-unit",
        "open-source-fixtures",
        "memory-baseline-locomo",
        "memory-baseline-longmemeval",
    ]
    assert any("pytest tests/benchmarks -m unit" in step for step in joined_steps)
    assert any(
        "pytest tests/integration/test_open_source_benchmark_integration.py "
        "-m integration --run-integration"
        in step
        for step in joined_steps
    )
    assert any("run_memory_baselines --benchmark locomo" in step for step in joined_steps)
    assert any(
        "run_memory_baselines --benchmark longmemeval" in step
        for step in joined_steps
    )
    assert not any("run_structure_benchmark" in step for step in joined_steps)


@pytest.mark.unit
def test_live_benchmark_integration_requires_credentials(monkeypatch):
    monkeypatch.delenv("STRUCTURE_BENCHMARK_TOKEN", raising=False)
    monkeypatch.delenv("STRUCTURE_BENCHMARK_USERNAME", raising=False)
    monkeypatch.delenv("STRUCTURE_BENCHMARK_PASSWORD", raising=False)
    args = runner.build_parser().parse_args(["--live"])

    with pytest.raises(SystemExit, match="missing STRUCTURE_BENCHMARK_TOKEN"):
        runner._build_steps(args)


@pytest.mark.unit
def test_benchmark_integration_writes_json_summary(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_run_command(command, *, cwd, env):
        calls.append(command.command)
        return runner.StepResult(
            name=command.name,
            command=command.command,
            returncode=0,
            elapsed_seconds=0.1234,
        )

    monkeypatch.setattr(runner, "_run_command", fake_run_command)
    output = tmp_path / "benchmark-summary.json"

    exit_code = runner.main(
        [
            "--skip-fixture",
            "--skip-baselines",
            "--output",
            str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert len(calls) == 1
    assert payload["status"] == "ok"
    assert payload["results"][0]["name"] == "benchmark-unit"
    assert payload["results"][0]["returncode"] == 0
