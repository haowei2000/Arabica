"""Unit tests for the core benchmark types + runner + metric aggregator.

Uses the LLM-free :class:`EchoAgent` so CI never touches an LLM
endpoint; the oracle variant (echo the reference) also doubles as a
wiring check for each benchmark's scorer.
"""

import asyncio

from benchmarks.baselines import EchoAgent
from benchmarks.core import (
    BenchmarkCase,
    BenchmarkRunner,
    CostLedger,
    EvidenceRecord,
    aggregate,
)
from benchmarks.core.types import BenchmarkResult
import pytest


@pytest.mark.unit
def test_cost_ledger_addition_is_elementwise():
    a = CostLedger(
        tokens_prompt=10,
        tokens_completion=5,
        tokens_cached=2,
        cache_creation_tokens=3,
        cache_read_tokens=4,
        steps=1,
        tool_calls=2,
    )
    b = CostLedger(
        tokens_prompt=3,
        tokens_completion=7,
        tokens_cached=5,
        cache_creation_tokens=6,
        cache_read_tokens=7,
        steps=4,
        latency_seconds=0.25,
    )
    total = a + b
    assert total.tokens_prompt == 13
    assert total.tokens_completion == 12
    assert total.tokens_total == 25
    assert total.tokens_cached == 7
    assert total.cache_creation_tokens == 9
    assert total.cache_read_tokens == 11
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
def test_aggregate_reports_efficiency_and_complexity_diagnostics():
    cases = [
        BenchmarkCase(task_id="a", inputs={}, reference="yes"),
        BenchmarkCase(task_id="b", inputs={}, reference="yes"),
    ]
    results = [
        BenchmarkResult(
            task_id="a",
            response="yes",
            cost=CostLedger(
                tokens_prompt=90,
                tokens_completion=10,
                tokens_cached=20,
                latency_seconds=4.0,
            ),
            metadata={
                "turn_count": 20,
                "available_context_tokens": 10_000,
                "selected_context_tokens": 1_000,
                "context_compression_ratio": 0.1,
                "available_chunks": 10,
                "selected_chunks": 2,
            },
        ),
        BenchmarkResult(
            task_id="b",
            response="no",
            cost=CostLedger(
                tokens_prompt=45,
                tokens_completion=5,
                latency_seconds=2.0,
            ),
            metadata={
                "turn_count": 0,
                "trajectory_count": 100,
                "available_context_tokens": 80_000,
                "selected_context_tokens": 8_000,
                "context_compression_ratio": 0.1,
                "available_chunks": 100,
                "selected_chunks": 6,
            },
        ),
    ]

    report = aggregate(
        "t",
        cases,
        results,
        scorer=lambda r, p: 1.0 if r == p else 0.0,
    )

    summary = report.diagnostic_summary
    assert summary["tokens_per_scored_point"] == pytest.approx(150.0)
    assert summary["latency_seconds_per_scored_point"] == pytest.approx(6.0)
    assert summary["mean_selected_chunks"] == pytest.approx(4.0)
    assert summary["kv_cache"]["tokens_cached"] == 20
    assert summary["kv_cache"]["reported_cases"] == 1
    assert summary["accuracy_by_context_tokens_bucket"]["4k-16k"] == {
        "n": 1,
        "accuracy": 1.0,
    }
    assert summary["accuracy_by_context_tokens_bucket"]["64k-128k"] == {
        "n": 1,
        "accuracy": 0.0,
    }


@pytest.mark.unit
def test_aggregate_raises_on_unknown_task_id():
    case = BenchmarkCase(task_id="known", inputs={}, reference="x")
    result = BenchmarkResult(task_id="unknown", response="x", cost=CostLedger())
    with pytest.raises(KeyError, match="unknown"):
        aggregate("t", [case], [result], scorer=lambda r, p: 1.0)


@pytest.mark.unit
def test_aggregate_reports_evidence_summary_and_score_bounds():
    cases = [
        BenchmarkCase(task_id="a", inputs={}, reference="yes", ability="qa"),
        BenchmarkCase(task_id="b", inputs={}, reference="yes", ability="qa"),
        BenchmarkCase(task_id="c", inputs={}, reference="yes", ability="qa"),
    ]
    results = [
        BenchmarkResult(
            task_id="a",
            response="yes",
            cost=CostLedger(steps=1),
            evidence=EvidenceRecord(status="pass", artifacts=("artifact://a",)),
        ),
        BenchmarkResult(
            task_id="b",
            response="yes",
            cost=CostLedger(steps=1),
            evidence=EvidenceRecord(status="unknown", notes="missing screenshot"),
        ),
        BenchmarkResult(
            task_id="c",
            response="no",
            cost=CostLedger(steps=1),
            evidence=EvidenceRecord(status="fail"),
        ),
    ]

    report = aggregate(
        "t",
        cases,
        results,
        scorer=lambda r, p: 1.0 if r == p else 0.0,
    )

    assert report.overall_score == pytest.approx(2 / 3)
    assert report.evidence_summary == {"pass": 1, "unknown": 1, "fail": 1}
    assert report.score_bounds == pytest.approx((1 / 3, 2 / 3))
    assert report.to_dict()["evidence_summary"] == {
        "pass": 1,
        "unknown": 1,
        "fail": 1,
    }
    assert report.to_dict()["score_bounds"] == pytest.approx((0.3333, 0.6667))


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
