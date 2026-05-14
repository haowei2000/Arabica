"""Tests for the real-LLM benchmark adapter using a fake client."""

import asyncio
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from benchmarks.baselines import LLMBenchmarkAgent
from benchmarks.core import BenchmarkRunner
from benchmarks.longmemeval import load_longmemeval, longmemeval_scorer
import pytest

REPO_ROOT = Path(__file__).parents[2]
FIXTURE = REPO_ROOT / "benchmarks" / "longmemeval" / "fixtures" / "sample.json"


class _FakeCompletions:
    async def create(self, **kwargs):
        prompt = kwargs["messages"][-1]["content"]
        if "2024-03-02" in prompt and "2024-04-27" in prompt:
            answer = "8"
        elif "Lisbon" in prompt:
            answer = "Lisbon"
        else:
            answer = "unknown"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=answer))],
            usage=SimpleNamespace(prompt_tokens=123, completion_tokens=4),
        )


class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


@pytest.mark.unit
def test_llm_agent_can_run_with_fake_openai_compatible_client():
    cases = load_longmemeval(FIXTURE)
    temporal_case = next(
        case for case in cases if case.ability == "temporal-reasoning"
    )
    agent = LLMBenchmarkAgent(
        model="fake-model",
        api_key="unused",
        client=_FakeClient(),
    )
    result = asyncio.run(agent.run(temporal_case))
    assert result.response == "8"
    assert result.cost.tokens_prompt == 123
    assert result.cost.tokens_completion == 4


@pytest.mark.unit
def test_llm_agent_scores_through_benchmark_runner_with_fake_client():
    cases = load_longmemeval(FIXTURE)[:1]
    runner = BenchmarkRunner(
        benchmark_name="longmemeval:llm-fake",
        agent=LLMBenchmarkAgent(
            model="fake-model",
            api_key="unused",
            client=_FakeClient(),
        ),
        scorer=longmemeval_scorer,
    )
    report = asyncio.run(runner.run(cases))
    assert report.overall_score == pytest.approx(1.0)
    assert report.total_cost.tokens_total == 127


@pytest.mark.unit
def test_llm_runner_requires_api_key_env():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.scripts.run_llm_benchmark",
            "--benchmark",
            "longmemeval",
            "--format",
            "json",
        ],
        capture_output=True,
        cwd=REPO_ROOT,
        env={},
        text=True,
    )
    assert completed.returncode != 0
    assert "missing API key" in completed.stderr


@pytest.mark.unit
def test_llm_runner_json_renderer_is_machine_readable():
    from benchmarks.scripts.run_llm_benchmark import render_json

    cases = load_longmemeval(FIXTURE)[:1]
    runner = BenchmarkRunner(
        benchmark_name="longmemeval:llm-fake",
        agent=LLMBenchmarkAgent(
            model="fake-model",
            api_key="unused",
            client=_FakeClient(),
        ),
        scorer=longmemeval_scorer,
    )
    report = asyncio.run(runner.run(cases))
    payload = json.loads(render_json(report))
    assert payload["benchmark"] == "longmemeval:llm-fake"
    assert payload["per_case"][0]["tokens_total"] == 127

