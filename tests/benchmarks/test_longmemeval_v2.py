"""Smoke tests for the LongMemEval-V2 adapter."""

import asyncio
from pathlib import Path

from benchmarks.baselines import EchoAgent
from benchmarks.baselines.memory_agents import evidence_from_selected_context
from benchmarks.core import BenchmarkCase, BenchmarkRunner
from benchmarks.longmemeval_v2 import (
    extract_trajectory_evidence,
    load_longmemeval_v2,
    longmemeval_v2_scorer,
    parse_eval_function,
    score_with_eval_function,
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
def test_eval_function_phrase_set_and_ordered_matching():
    spec = (
        "norm_phrase_set_match|lower=true|normalize_hyphen=true|"
        "strip_punct=true|separators=,;|require_non_empty=true"
    )
    reference = {
        "answer": "Incident Mobile, Incident Portal, My Open Incidents",
        "eval_function": spec,
    }
    assert parse_eval_function(spec).name == "norm_phrase_set_match"
    assert score_with_eval_function(
        reference,
        '{"answer":"my open incidents; incident portal; incident-mobile"}',
    ) == pytest.approx(1.0)
    assert score_with_eval_function(reference, "Incident Portal") == pytest.approx(0.0)

    ordered = {
        "answer": "three, Actions, Change Status, Disable",
        "eval_function": spec.replace(
            "norm_phrase_set_match", "norm_phrase_set_match_ordered"
        ),
    }
    assert score_with_eval_function(
        ordered,
        {"answer": "three; Actions; Change Status; Disable"},
    ) == pytest.approx(1.0)
    assert score_with_eval_function(
        ordered,
        {"answer": "Actions; three; Change Status; Disable"},
    ) == pytest.approx(0.0)


@pytest.mark.unit
def test_eval_function_mc_choice_matching():
    assert score_with_eval_function(
        {"answer": "G", "eval_function": "mc_choice_match|require_non_empty=true"},
        {"answer": "The answer is G."},
    ) == pytest.approx(1.0)
    assert score_with_eval_function(
        {
            "answer": "A,B,F",
            "eval_function": "mc_choice_set_match|require_non_empty=true",
        },
        {"answer": "F, A, B"},
    ) == pytest.approx(1.0)


@pytest.mark.unit
def test_trajectory_evidence_helper_renders_stable_state_ids():
    trajectory = {
        "id": "traj-xyz",
        "states": [
            {
                "state_index": 0,
                "thought": "Open the incident list",
                "accessibility_tree": "Incident menu",
                "screenshot": "screenshots/traj-xyz/0.png",
            },
            {
                "state_index": 3,
                "thought": "Find Adobe Photoshop in hardware configuration",
                "accessibility_tree": "Adobe Photoshop checkbox",
                "screenshot": "screenshots/traj-xyz/3.png",
            },
        ],
    }

    evidence = extract_trajectory_evidence(
        trajectory,
        question="Which hardware configuration includes Adobe Photoshop?",
        max_states=1,
    )

    assert evidence == [
        (
            "traj-xyz:s3",
            "evidence_id: traj-xyz:s3\n"
            "state: 3\n"
            "screenshot: screenshots/traj-xyz/3.png\n"
            "thought: Find Adobe Photoshop in hardware configuration\n"
            "observation: Adobe Photoshop checkbox",
        )
    ]


@pytest.mark.unit
def test_evidence_audit_uses_response_state_citations_without_gold_ids():
    case = BenchmarkCase(
        task_id="q1",
        inputs={"question": "Where was Photoshop selected?"},
        reference={
            "answer": "hardware configuration",
            "evidence_ids": [],
            "eval_function": "llm_gotchas_checker|require_non_empty=true",
        },
    )

    record = evidence_from_selected_context(
        case,
        [("traj-xyz", "evidence_id: traj-xyz:s3\nobservation: Photoshop checkbox")],
        response='{"answer":"hardware configuration","evidence_ids":["traj-xyz:s3"]}',
    )

    assert record.status == "pass"
    assert record.artifacts == ("traj-xyz:s3",)


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
