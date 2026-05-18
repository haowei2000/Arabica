"""Tests for full memory benchmark runner utilities."""

from benchmarks.core import BenchmarkCase
from benchmarks.scripts.run_full_memory_benchmark import (
    metric_definitions,
    render_markdown,
    sample_cases,
)
import pytest


def _cases(count: int) -> list[BenchmarkCase]:
    return [
        BenchmarkCase(task_id=f"case-{index:03d}", inputs={}, reference="answer")
        for index in range(count)
    ]


@pytest.mark.unit
def test_sample_cases_supports_stable_hash_percent_sampling():
    cases = _cases(100)

    first, first_info = sample_cases(
        cases,
        sample_mode="hash",
        sample_seed="seed-a",
        sample_percent=10,
    )
    second, second_info = sample_cases(
        cases,
        sample_mode="hash",
        sample_seed="seed-a",
        sample_percent=10,
    )
    different, _ = sample_cases(
        cases,
        sample_mode="hash",
        sample_seed="seed-b",
        sample_percent=10,
    )

    assert [case.task_id for case in first] == [case.task_id for case in second]
    assert [case.task_id for case in first] != [case.task_id for case in different]
    assert first_info["sample_size"] == 10
    assert first_info == second_info


@pytest.mark.unit
def test_sample_cases_supports_seeded_random_and_max_case_cap():
    cases = _cases(50)

    sampled, info = sample_cases(
        cases,
        sample_mode="random",
        sample_seed="daily",
        sample_percent=20,
        max_cases=3,
    )

    assert len(sampled) == 3
    assert info["sample_size"] == 3
    assert info["source_cases"] == 50
    assert [case.task_id for case in sampled] == sorted(
        case.task_id for case in sampled
    )


@pytest.mark.unit
def test_markdown_report_includes_efficiency_metrics_and_external_baselines():
    rendered = render_markdown(
        [
            {
                "benchmark": "longmemeval-v2-small:StructureMemory:fake",
                "method": "StructureMemory",
                "sample": {
                    "sample_size": 46,
                    "source_cases": 451,
                    "sample_percent_effective": 10.2,
                },
                "overall_score": 0.05,
                "evidence_summary": {"unknown": 46},
                "mean_cost": {
                    "tokens_prompt": 100,
                    "tokens_completion": 10,
                    "latency_seconds": 1.25,
                },
                "total_cost": {
                    "tokens_prompt": 4600,
                    "tokens_completion": 460,
                    "usd_cost": 0.0,
                },
                "diagnostic_summary": {
                    "mean_selected_chunks": 6,
                    "mean_available_chunks": 100,
                    "mean_context_compression_ratio": 0.06,
                    "tokens_per_scored_point": 2200.0,
                    "latency_seconds_per_scored_point": 25.0,
                    "kv_cache": {"tokens_cached": 0},
                },
                "external_baseline_comparison": [
                    {
                        "method": "AgentRunbook-C",
                        "score": 0.749,
                        "metric_name": "accuracy",
                        "source_title": "LongMemEval-V2 project leaderboard",
                        "source_url": "https://xiaowu0162.github.io/longmemeval-v2/",
                        "score_delta_vs_local": -0.699,
                        "comparability": "calibration-only",
                    }
                ],
            }
        ]
    )

    assert "Tok/score" in rendered
    assert "KV cached" in rendered
    assert "AgentRunbook-C" in rendered
    assert "External paper/project baselines" in rendered


@pytest.mark.unit
def test_metric_definitions_document_cache_and_complexity_fields():
    definitions = metric_definitions()
    assert "tokens_cached" in definitions
    assert "turn_count" in definitions
    assert "external_baseline_comparison" in definitions
