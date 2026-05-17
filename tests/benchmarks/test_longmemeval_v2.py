"""Smoke tests for the LongMemEval-V2 adapter."""

import asyncio
from pathlib import Path

from benchmarks.baselines import EchoAgent
from benchmarks.core import BenchmarkRunner
from benchmarks.longmemeval_v2 import (
    load_longmemeval_v2,
    longmemeval_v2_scorer,
)
import pytest

FIXTURE = (
    Path(__file__).parent.parent.parent
    / "benchmarks"
    / "longmemeval_v2"
    / "fixtures"
    / "sample.json"
)


@pytest.mark.unit
def test_loader_preserves_insert_query_memory_shape():
    cases = load_longmemeval_v2(FIXTURE)

    assert len(cases) == 3
    first = cases[0]
    assert first.task_id == "lme-v2-smoke-001"
    assert first.ability == "environment-experience"
    assert first.inputs["memory_protocol"] == "insert_trajectories_then_query"
    assert first.inputs["evidence_ids"] == ["traj-001:e3"]
    assert first.reference == {
        "answer": "Northstar CRM",
        "evidence_ids": ["traj-001:e3"],
    }
    assert first.inputs["trajectories"][0]["trajectory_id"] == "traj-001"


@pytest.mark.unit
def test_scorer_blends_answer_match_and_evidence_recall():
    reference = {
        "answer": "Northstar CRM",
        "evidence_ids": ["traj-001:e3", "traj-002:e1"],
    }

    assert longmemeval_v2_scorer(reference, reference) == pytest.approx(1.0)
    assert longmemeval_v2_scorer(
        reference,
        {
            "answer": "Northstar CRM",
            "evidence_ids": ["traj-001:e3"],
        },
    ) == pytest.approx(0.9)
    assert longmemeval_v2_scorer(
        reference,
        {
            "answer": "Northstar CRM",
            "evidence_ids": [],
        },
    ) == pytest.approx(0.8)
    assert longmemeval_v2_scorer(
        reference,
        {
            "answer": "AtlasCRM",
            "evidence_ids": ["traj-001:e3", "traj-002:e1"],
        },
    ) == pytest.approx(0.2)


@pytest.mark.unit
def test_oracle_run_over_fixture_scores_perfect():
    cases = load_longmemeval_v2(FIXTURE)
    runner = BenchmarkRunner(
        benchmark_name="longmemeval-v2-smoke",
        agent=EchoAgent(),
        scorer=longmemeval_v2_scorer,
    )

    report = asyncio.run(runner.run(cases))

    assert report.n_cases == 3
    assert report.overall_score == pytest.approx(1.0)
    assert set(report.per_ability_score) == {
        "environment-experience",
        "cross-trajectory",
        "safety-evidence",
    }
