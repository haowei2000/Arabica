"""End-to-end smoke test for the LongMemEval adapter.

The oracle :class:`EchoAgent` must produce ``overall_score == 1.0`` on
the bundled fixture. If this breaks, either the loader lost the
reference or the scorer's normalisation became too strict.
"""

import asyncio
from pathlib import Path

import pytest

from benchmarks.baselines import EchoAgent
from benchmarks.core import BenchmarkRunner
from benchmarks.longmemeval import load_longmemeval, longmemeval_scorer
from benchmarks.longmemeval.scorer import _normalise

FIXTURE = Path(__file__).parent.parent.parent / "benchmarks" / "longmemeval" / "fixtures" / "sample.json"


@pytest.mark.unit
def test_loader_parses_fixture_and_preserves_ability_tags():
    cases = load_longmemeval(FIXTURE)
    assert len(cases) == 4
    abilities = {c.ability for c in cases}
    assert abilities == {
        "single-session-user",
        "multi-session",
        "temporal-reasoning",
        "knowledge-update",
    }
    first = cases[0]
    assert first.task_id == "lme-smoke-001"
    assert "Lisbon" in first.inputs["sessions"][0][0]["content"]
    assert first.reference == "Lisbon"


@pytest.mark.unit
def test_scorer_accepts_substring_match_and_case_insensitive():
    # Single gold --- lowercase substring match.
    assert longmemeval_scorer("Lisbon", "The user moved to lisbon.") == 1.0
    # Multi-gold --- any of the accepted answers is enough.
    assert longmemeval_scorer(["Rust", "rustc"], "They picked Rust.") == 1.0
    # Mismatch --- scorer is substring-based, not semantic, so this is 0.
    assert longmemeval_scorer("Lisbon", "they moved to madrid") == 0.0
    # Empty response never scores.
    assert longmemeval_scorer("anything", "") == 0.0


@pytest.mark.unit
def test_scorer_requires_literal_overlap_not_semantic_equivalence():
    # LongMemEval's deterministic scorer does not know "eight" == "8";
    # callers who need that equivalence should provide both spellings
    # in the reference list (as the fixture does for lme-smoke-003).
    assert longmemeval_scorer("eight", "it was 8 weeks") == 0.0
    assert longmemeval_scorer(["8", "eight"], "it was 8 weeks") == 1.0


@pytest.mark.unit
def test_scorer_punctuation_normalisation_does_not_mask_content():
    # Punctuation should be stripped; meaningful tokens should remain.
    assert _normalise("Genmaicha!") == "genmaicha"
    assert longmemeval_scorer("genmaicha", "Genmaicha!") == 1.0


@pytest.mark.unit
def test_oracle_run_over_fixture_scores_perfect():
    cases = load_longmemeval(FIXTURE)
    runner = BenchmarkRunner(
        benchmark_name="longmemeval-smoke",
        agent=EchoAgent(),
        scorer=longmemeval_scorer,
    )
    report = asyncio.run(runner.run(cases))
    assert report.n_cases == 4
    assert report.overall_score == pytest.approx(1.0)
    # Every ability category in the fixture should appear in the breakdown.
    assert set(report.per_ability_score) == {
        "single-session-user",
        "multi-session",
        "temporal-reasoning",
        "knowledge-update",
    }
    assert all(v == pytest.approx(1.0) for v in report.per_ability_score.values())


@pytest.mark.unit
def test_oracle_with_wrong_policy_scores_zero():
    cases = load_longmemeval(FIXTURE)
    runner = BenchmarkRunner(
        benchmark_name="longmemeval-neg",
        agent=EchoAgent(policy=lambda case: "completely unrelated response"),
        scorer=longmemeval_scorer,
    )
    report = asyncio.run(runner.run(cases))
    assert report.overall_score == pytest.approx(0.0)
