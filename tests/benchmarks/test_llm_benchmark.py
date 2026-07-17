"""Tests for the real-LLM benchmark adapter using a fake client."""

import asyncio
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from benchmarks.baselines import LLMBenchmarkAgent
from benchmarks.core import BenchmarkCase, BenchmarkRunner
from benchmarks.longmemeval import load_longmemeval, longmemeval_scorer
from benchmarks.scripts.run_full_memory_benchmark import _make_agent, build_parser
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
            usage=SimpleNamespace(
                prompt_tokens=123,
                completion_tokens=4,
                prompt_tokens_details=SimpleNamespace(cached_tokens=12),
            ),
        )


class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


@pytest.mark.unit
def test_llm_agent_can_run_with_fake_openai_compatible_client():
    cases = load_longmemeval(FIXTURE)
    temporal_case = next(case for case in cases if case.ability == "temporal-reasoning")
    agent = LLMBenchmarkAgent(
        model="fake-model",
        api_key="unused",
        client=_FakeClient(),
    )
    result = asyncio.run(agent.run(temporal_case))
    assert result.response == "8"
    assert result.cost.tokens_prompt == 123
    assert result.cost.tokens_completion == 4
    assert result.cost.tokens_cached == 12
    assert result.cost.cache_read_tokens == 12
    assert result.metadata["available_chunks"] >= result.metadata["selected_chunks"]
    assert "available_context_tokens" in result.metadata


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
def test_llm_runner_requires_openai_env_contract():
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
    assert "missing LLM environment variables" in completed.stderr
    assert "OPENAI__API_KEY" in completed.stderr
    assert "OPENAI__BASE_URL" in completed.stderr
    assert "OPENAI__MODEL" in completed.stderr


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


class _RecordingCompletions:
    def __init__(self) -> None:
        self.messages = None

    async def create(self, **kwargs):
        self.messages = kwargs["messages"]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Paris"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2),
        )


class _RecordingClient:
    def __init__(self):
        self.completions = _RecordingCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


@pytest.mark.unit
def test_closedbook_prompt_contains_question_but_no_context():
    cases = load_longmemeval(FIXTURE)[:1]
    client = _RecordingClient()
    agent = LLMBenchmarkAgent(
        model="fake-model",
        api_key="unused",
        context_mode="closedbook",
        client=client,
    )
    result = asyncio.run(agent.run(cases[0]))

    system, user = client.completions.messages
    assert "context" not in system["content"].lower()
    assert "Context:" not in user["content"]
    assert str(cases[0].inputs["question"]) in user["content"]
    assert result.metadata["context_mode"] == "closedbook"
    assert result.metadata["selected_chunks"] == 0
    assert result.metadata["available_chunks"] > 0


@pytest.mark.unit
def test_closedbook_keeps_json_shape_for_evidence_benchmarks():
    case = BenchmarkCase(
        task_id="v2-style",
        inputs={"question": "Where does Ana live?", "sessions": [["Ana: hi"]]},
        reference={"answer": "Lisbon", "evidence_ids": ["e1"]},
    )
    client = _RecordingClient()
    agent = LLMBenchmarkAgent(
        model="fake-model",
        api_key="unused",
        context_mode="closedbook",
        client=client,
    )
    asyncio.run(agent.run(case))

    system, _user = client.completions.messages
    assert "evidence_ids" in system["content"]
    assert "leave" in system["content"].lower()
    assert "bracketed" not in system["content"]


@pytest.mark.unit
def test_runner_exposes_closedbook_method():
    args = build_parser().parse_args(
        ["--benchmark", "locomo", "--methods", "ClosedBook"]
    )
    assert args.methods == ["ClosedBook"]

    agent = _make_agent(
        method="ClosedBook",
        api_key="test-key",
        base_url="http://example.test/v1",
        model="fake-model",
        top_k=6,
        temperature=0.0,
        max_context_chars=120_000,
        input_cost_per_mtok=0.0,
        output_cost_per_mtok=0.0,
        output_dir=Path("/tmp/unused"),
    )
    assert isinstance(agent, LLMBenchmarkAgent)
    assert agent.context_mode == "closedbook"
