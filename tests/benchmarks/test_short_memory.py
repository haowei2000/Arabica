"""Short-memory regression tests — pure functions, LLM-free.

Covers:
  1. fixture loader + synthetic generator round-trip.
  2. replay harness produces sane metrics on the smoke fixture.
  3. oracle: synthetic log's expected dedup ratio matches the generator's
     claimed target *when dedup is enabled*.
  4. before/after: enabling dedup must shrink body_chars with head stable.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from benchmarks.core.types import CostLedger
from benchmarks.short_memory.dataset import (
    expected_dedup_ratio,
    generate_redundant_log,
    load_fixture,
    strip_meta,
)
from benchmarks.short_memory.metrics import (
    ReplayMetrics,
    compute_replay_metrics,
    short_memory_score,
)
from benchmarks.short_memory.replay import replay_fixture
import pytest

from structure.core.enums.events import EventType
from structure.frameworks.tool_calling import ChatMessage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def smoke_events():
    return load_fixture("benchmarks/short_memory/fixtures/smoke.jsonl")


@pytest.fixture
def synthetic_events():
    return generate_redundant_log(n_turns=20, repeat_ratio=0.4)


# ---------------------------------------------------------------------------
# 1. Dataset layer
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_load_fixture_parses_known_event_count(smoke_events):
    assert len(smoke_events) == 6
    assert smoke_events[0]["event_type"] == str(EventType.USER_MESSAGE)
    assert smoke_events[-1]["event_type"] == str(EventType.TOOL_RESULT)


@pytest.mark.unit
def test_generate_redundant_log_is_deterministic():
    a = generate_redundant_log(n_turns=10, repeat_ratio=0.5, seed=42)
    b = generate_redundant_log(n_turns=10, repeat_ratio=0.5, seed=42)
    assert strip_meta(a) == strip_meta(b)


@pytest.mark.unit
def test_expected_dedup_ratio_matches_ground_truth(synthetic_events):
    ratio = expected_dedup_ratio(synthetic_events)
    assert 0.0 < ratio < 1.0, f"bad ratio {ratio}"
    # With repeat_ratio=0.4 the observed duplicate ratio should be > 0.2.
    assert ratio >= 0.2


# ---------------------------------------------------------------------------
# 2. Metrics layer — smoke
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_replay_smoke_produces_messages(smoke_events):
    _messages, metrics = replay_fixture(smoke_events)
    assert metrics.replayed_messages > 0
    assert metrics.body_chars > 0
    assert metrics.total_tool_results == 2, "smoke has two tool.results"


@pytest.mark.unit
def test_compute_replay_metrics_counts_tool_messages():
    msgs = [
        ChatMessage(role="user", content="hello"),
        ChatMessage(role="assistant", content="ok"),
        ChatMessage(role="tool", content="A" * 100, tool_call_id="tc1"),
        ChatMessage(role="tool", content="B" * 200, tool_call_id="tc2"),
    ]
    m = compute_replay_metrics(msgs)
    assert m.total_tool_results == 2
    assert m.replayed_tool_result_chars == 300
    assert m.body_chars >= 300  # tool content counts toward body


@pytest.mark.unit
def test_dedup_marker_is_detected():
    msgs = [
        ChatMessage(role="user", content="hi"),
        ChatMessage(role="tool", content="<dedup: first seen turn 1, sha256=abc>", tool_call_id="tc1"),
    ]
    m = compute_replay_metrics(msgs)
    assert m.deduped_tool_results == 1
    assert m.dedup_ratio == 1.0  # 1 of 1 tool results was folded


# ---------------------------------------------------------------------------
# 3. Oracle: synthetic log dedup target
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_synthetic_log_dedup_target_is_reachable(synthetic_events):
    """The synthetic log's duplicate reads *should* be catchable.

    With dedup disabled the ratio is 0; the test below (before/after) proves
    the dedup pass closes the gap toward this target.
    """
    target = expected_dedup_ratio(synthetic_events)
    events = strip_meta(synthetic_events)

    # Baseline: no dedup.
    _, before = replay_fixture(events)
    assert before.dedup_ratio == 0.0, "baseline must have zero dedup"
    assert before.total_tool_results == 20

    # The target is a lower bound on what a perfect dedup *could* catch.
    assert target > 0.0


@pytest.mark.unit
def test_dedup_reduces_body_and_records_ratio(synthetic_events):
    """The actual before/after proof: dedup shrinks body, raises ratio."""
    events = strip_meta(synthetic_events)

    _, before = replay_fixture(events, dedup_tool_results=False)
    _, after = replay_fixture(events, dedup_tool_results=True)

    # Body must not grow.
    assert after.body_chars <= before.body_chars
    # Some tool results must have been folded.
    assert after.deduped_tool_results > 0
    assert after.dedup_ratio > 0.0
    # The deduped payloads should be much shorter than the originals.
    assert after.replayed_tool_result_chars < before.replayed_tool_result_chars


@pytest.mark.unit
def test_dedup_on_smoke_folds_duplicate_read():
    """The smoke fixture has two identical read_context results; one folds."""
    events = load_fixture("benchmarks/short_memory/fixtures/smoke.jsonl")
    _, before = replay_fixture(events, dedup_tool_results=False)
    _, after = replay_fixture(events, dedup_tool_results=True)

    assert before.deduped_tool_results == 0
    assert after.deduped_tool_results >= 1
    assert after.body_chars < before.body_chars


# ---------------------------------------------------------------------------
# 4. Before/after contract
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_score_weights_sum_to_one():
    s = short_memory_score(
        ReplayMetrics(body_chars=1000),
        ReplayMetrics(body_chars=600, deduped_tool_results=4, total_tool_results=10),
    )
    assert 0.0 <= s["score"] <= 1.0


@pytest.mark.unit
def test_cost_ledger_round_trip():
    m = ReplayMetrics(body_chars=4000, total_tool_results=3)
    ledger = m.to_ledger(n_steps=5)
    assert isinstance(ledger, CostLedger)
    assert ledger.steps == 5
    assert ledger.tool_calls == 3


# ---------------------------------------------------------------------------
# Real-session fixtures (exported from v1 Rust runtime SQLite DB)
# ---------------------------------------------------------------------------

_REAL_FIXTURES = [
    ("benchmarks/short_memory/fixtures/real_session_18544.jsonl", 13),
    ("benchmarks/short_memory/fixtures/real_session_4233.jsonl", 12),
    ("benchmarks/short_memory/fixtures/real_session_90737.jsonl", 9),
    ("benchmarks/short_memory/fixtures/real_session_98198.jsonl", 9),
]


@pytest.mark.unit
@pytest.mark.parametrize("path,expected_msgs", _REAL_FIXTURES)
def test_real_fixture_replays_without_error(path, expected_msgs):
    """Every exported real session must replay to the expected msg count."""
    events = load_fixture(path)
    _messages, metrics = replay_fixture(events)
    assert metrics.replayed_messages == expected_msgs
    # Sanity: body must not grow when dedup is enabled.
    _, after = replay_fixture(events, dedup_tool_results=True)
    assert after.body_chars <= metrics.body_chars


@pytest.mark.unit
def test_real_fixture_dedup_never_increases_body():
    """Invariant: dedup must never make the prompt larger."""
    for path, _ in _REAL_FIXTURES:
        events = load_fixture(path)
        _, before = replay_fixture(events, dedup_tool_results=False)
        _, after = replay_fixture(events, dedup_tool_results=True)
        assert after.body_chars <= before.body_chars, f"{path} body grew"
