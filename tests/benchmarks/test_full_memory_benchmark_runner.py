"""Tests for full memory benchmark runner utilities."""

from benchmarks.core import BenchmarkCase
from benchmarks.scripts.run_full_memory_benchmark import sample_cases
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
