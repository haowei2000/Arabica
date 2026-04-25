"""Unit tests for the core benchmark types + runner + metric aggregator.

Uses the LLM-free :class:`EchoAgent` so CI never touches an LLM
endpoint; the oracle variant (echo the reference) also doubles as a
wiring check for each benchmark's scorer.
"""

import asyncio

import pytest

from benchmarks.baselines import EchoAgent
from benchmarks.core import (
    BenchmarkCase,
    BenchmarkRunner,
    CostLedger,
    aggregate,
)
from benchmarks.core.types import BenchmarkResult


@pytest.mark.unit
def test_cost_ledger_addition_is_elementwise():
    a = CostLedger(tokens_prompt=10, tokens_completion=5, steps=1, tool_calls=2)
    b = CostLedger(tokens_prompt=3, tokens_completion=7, steps=4, latency_seconds=0.25)
    total = a + b
    assert total.tokens_prompt == 13
    assert total.tokens_completion == 12
    assert total.tokens_total == 25
    assert total.steps == 5
    assert total.tool_calls == 2
    assert total.latency_seconds == pytest.approx(0.25)


@pytest.mark.unit
def test_aggregate_pairs_by_task_id_and_computes_per_ability():
    cases = [
        BenchmarkCase(
            task_id="a",
            inputs={},
            reference="yes",
            ability="single-session",
        ),
        BenchmarkCase(
            task_id="b",
            inputs={},
            reference="no",
            ability="multi-session",
        ),
    ]
    results = [
        BenchmarkResult(task_id="a", response="yes", cost=CostLedger(steps=1)),
        BenchmarkResult(task_id="b", response="wrong", cost=CostLedger(steps=2)),
    ]

    report = aggregate("t", cases, results, scorer=lambda r, p: 1.0 if r == p else 0.0)

    assert report.n_cases == 2
    assert report.overall_score == pytest.approx(0.5)
    assert report.per_ability_score["single-session"] == pytest.approx(1.0)
    assert report.per_ability_score["multi-session"] == pytest.approx(0.0)
    assert report.total_cost.steps == 3


@pytest.mark.unit
def test_aggregate_raises_on_unknown_task_id():
    case = BenchmarkCase(task_id="known", inputs={}, reference="x")
    result = BenchmarkResult(task_id="unknown", response="x", cost=CostLedger())
    with pytest.raises(KeyError, match="unknown"):
        aggregate("t", [case], [result], scorer=lambda r, p: 1.0)


@pytest.mark.unit
def test_runner_produces_perfect_score_with_oracle_echo_agent():
    cases = [
        BenchmarkCase(task_id=f"c{i}", inputs={}, reference=f"answer-{i}")
        for i in range(3)
    ]
    runner = BenchmarkRunner(
        benchmark_name="oracle-smoke",
        agent=EchoAgent(),  # default policy: echo the reference
        scorer=lambda ref, pred: 1.0 if ref == pred else 0.0,
    )
    report = asyncio.run(runner.run(cases))
    assert report.overall_score == pytest.approx(1.0)
    assert report.total_cost.steps == 3


@pytest.mark.unit
def test_runner_on_case_callback_fires_per_case():
    cases = [BenchmarkCase(task_id="x", inputs={}, reference="r")]
    observed: list[tuple[str, str]] = []
    runner = BenchmarkRunner(
        benchmark_name="cb",
        agent=EchoAgent(),
        scorer=lambda r, p: 1.0 if r == p else 0.0,
        on_case=lambda case, result: observed.append(
            (case.task_id, str(result.response))
        ),
    )
    asyncio.run(runner.run(cases))
    assert observed == [("x", "r")]
