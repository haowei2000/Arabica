"""Smoke tests for the LoCoMo adapter."""

import asyncio
from pathlib import Path

from benchmarks.baselines import EchoAgent, make_memory_baseline
from benchmarks.core import BenchmarkRunner
from benchmarks.locomo import load_locomo, locomo_qa_scorer
import pytest

FIXTURE = (
    Path(__file__).parent.parent.parent
    / "benchmarks"
    / "locomo"
    / "fixtures"
    / "sample.json"
)


@pytest.mark.unit
def test_loader_expands_conversation_qa_pairs():
    cases = load_locomo(FIXTURE)
    assert len(cases) == 3
    assert {case.ability for case in cases} == {
        "single-hop",
        "multi-hop",
        "temporal-update",
    }

    first = cases[0]
    assert first.task_id == "locomo-smoke-001:pet-name"
    assert first.inputs["question"] == "What is the user's sourdough starter named?"
    assert len(first.inputs["sessions"]) == 2
    assert first.reference == "Pixel"


@pytest.mark.unit
def test_loader_supports_upstream_session_dict_and_adversarial_answer(tmp_path):
    dataset = tmp_path / "locomo.json"
    dataset.write_text(
        """
        [
          {
            "sample_id": "conv-x",
            "conversation": {
              "session_1_date_time": "1:56 pm on 8 May, 2023",
              "session_1": [
                {"speaker": "A", "text": "I went to a LGBTQ support group yesterday."}
              ]
            },
            "qa": [
              {
                "question": "When did Caroline go to the LGBTQ support group?",
                "adversarial_answer": "7 May 2023",
                "evidence": ["D1:1"],
                "category": 5
              }
            ]
          }
        ]
        """,
        encoding="utf-8",
    )

    [case] = load_locomo(dataset)
    assert case.reference == "7 May 2023"
    assert case.ability == "5"
    assert case.metadata["evidence"] == ["D1:1"]
    assert (
        case.inputs["sessions"][0][0]["content"]
        == "session date: 1:56 pm on 8 May, 2023"
    )


@pytest.mark.unit
def test_locomo_scorer_exact_substring_and_token_f1():
    assert locomo_qa_scorer("Lantern", "The current codename is Lantern.") == 1.0
    assert locomo_qa_scorer(["bread flour"], "bread flour works best") == 1.0
    assert locomo_qa_scorer("bread flour", "bread") == pytest.approx(2 / 3)
    assert locomo_qa_scorer("Lantern", "Harbor") == 0.0


@pytest.mark.unit
def test_oracle_echo_scores_perfect_on_locomo_fixture():
    cases = load_locomo(FIXTURE)
    runner = BenchmarkRunner(
        benchmark_name="locomo-smoke",
        agent=EchoAgent(),
        scorer=locomo_qa_scorer,
    )
    report = asyncio.run(runner.run(cases))
    assert report.n_cases == 3
    assert report.overall_score == pytest.approx(1.0)


@pytest.mark.unit
def test_memory_baselines_find_fixture_evidence():
    cases = load_locomo(FIXTURE)
    for baseline in ("FullText", "NaiveRAG"):
        runner = BenchmarkRunner(
            benchmark_name=f"locomo:{baseline}",
            agent=make_memory_baseline(baseline, top_k=2),
            scorer=locomo_qa_scorer,
        )
        report = asyncio.run(runner.run(cases))
        assert report.overall_score == pytest.approx(1.0)
        assert report.mean_cost.tokens_total > 0
