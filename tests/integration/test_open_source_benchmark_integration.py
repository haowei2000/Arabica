"""Integration coverage for open-source benchmark adapters.

These tests keep the benchmark harness wired into integration CI with small,
checked-in fixtures. Full upstream suites remain optional because several of
them require external datasets, Docker/browser/VM infrastructure, or API keys.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from benchmarks.baselines import EchoAgent
from benchmarks.core import BenchmarkCase, BenchmarkRunner
from benchmarks.locomo import load_locomo, locomo_qa_scorer
from benchmarks.longmemeval import load_longmemeval, longmemeval_scorer
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).parents[2]


@dataclass(frozen=True)
class OpenSourceBenchmarkTarget:
    name: str
    upstream_url: str
    fixture_path: Path | None
    loader: Callable[[Path], list[BenchmarkCase]] | None
    scorer: Callable[[object, object], float] | None
    expected_cases: int | None
    mode: str


OPEN_SOURCE_BENCHMARK_TARGETS = (
    OpenSourceBenchmarkTarget(
        name="longmemeval",
        upstream_url="https://github.com/xiaowu0162/LongMemEval",
        fixture_path=REPO_ROOT / "benchmarks/longmemeval/fixtures/sample.json",
        loader=load_longmemeval,
        scorer=longmemeval_scorer,
        expected_cases=4,
        mode="local_fixture",
    ),
    OpenSourceBenchmarkTarget(
        name="locomo",
        upstream_url="https://github.com/snap-research/locomo",
        fixture_path=REPO_ROOT / "benchmarks/locomo/fixtures/sample.json",
        loader=load_locomo,
        scorer=locomo_qa_scorer,
        expected_cases=3,
        mode="local_fixture",
    ),
    OpenSourceBenchmarkTarget(
        name="tau-bench",
        upstream_url="https://github.com/sierra-research/tau-bench",
        fixture_path=None,
        loader=None,
        scorer=None,
        expected_cases=None,
        mode="distilled_db_trace",
    ),
    OpenSourceBenchmarkTarget(
        name="bfcl",
        upstream_url="https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard",
        fixture_path=None,
        loader=None,
        scorer=None,
        expected_cases=None,
        mode="distilled_db_trace",
    ),
    OpenSourceBenchmarkTarget(
        name="terminal-bench",
        upstream_url="https://github.com/laude-institute/terminal-bench",
        fixture_path=None,
        loader=None,
        scorer=None,
        expected_cases=None,
        mode="distilled_db_trace",
    ),
    OpenSourceBenchmarkTarget(
        name="swe-bench",
        upstream_url="https://github.com/swe-bench/SWE-bench",
        fixture_path=None,
        loader=None,
        scorer=None,
        expected_cases=None,
        mode="external_opt_in",
    ),
    OpenSourceBenchmarkTarget(
        name="webarena-osworld",
        upstream_url="https://github.com/web-arena-x/webarena",
        fixture_path=None,
        loader=None,
        scorer=None,
        expected_cases=None,
        mode="external_opt_in",
    ),
)


@pytest.mark.parametrize(
    "target",
    [
        target
        for target in OPEN_SOURCE_BENCHMARK_TARGETS
        if target.mode == "local_fixture"
    ],
    ids=lambda target: target.name,
)
def test_open_source_benchmark_fixtures_run_through_common_harness(
    target: OpenSourceBenchmarkTarget,
):
    assert target.fixture_path is not None
    assert target.loader is not None
    assert target.scorer is not None

    cases = target.loader(target.fixture_path)
    runner = BenchmarkRunner(
        benchmark_name=f"{target.name}:integration-fixture",
        agent=EchoAgent(),
        scorer=target.scorer,
    )

    report = runner.run_sync(cases)

    assert report.n_cases == target.expected_cases
    assert report.overall_score == pytest.approx(1.0)
    assert report.total_cost.steps == target.expected_cases
    assert report.mean_cost.steps == 1
    assert report.per_ability_score


def test_open_source_agent_benchmark_integration_policy_is_explicit():
    modes_by_name = {
        target.name: target.mode for target in OPEN_SOURCE_BENCHMARK_TARGETS
    }

    assert modes_by_name["longmemeval"] == "local_fixture"
    assert modes_by_name["locomo"] == "local_fixture"
    assert modes_by_name["tau-bench"] == "distilled_db_trace"
    assert modes_by_name["bfcl"] == "distilled_db_trace"
    assert modes_by_name["terminal-bench"] == "distilled_db_trace"
    assert modes_by_name["swe-bench"] == "external_opt_in"
    assert modes_by_name["webarena-osworld"] == "external_opt_in"
    assert all(
        target.upstream_url.startswith("https://")
        for target in OPEN_SOURCE_BENCHMARK_TARGETS
    )
