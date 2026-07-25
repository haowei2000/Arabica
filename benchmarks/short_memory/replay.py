"""Replay harness: run ``_events_to_messages`` on a fixture and measure.

This is the single entry point the tests and the future CLI both use.  It
deliberately does **not** import the live executor — it calls the pure
replay function directly so the benchmark stays LLM-free.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from benchmarks.short_memory.metrics import ReplayMetrics, compute_replay_metrics
from structure.plugins.executors.default.concrete import _events_to_messages


def _dict_to_event(e: dict[str, Any]) -> SimpleNamespace:
    """Minimal event stand-in compatible with ``_events_to_messages``.

    The function only reads ``e.event_type``, ``e.payload``, ``e.sequence``.
    """
    return SimpleNamespace(
        event_type=e.get("event_type", ""),
        payload=e.get("payload", {}) or {},
        sequence=int(e.get("sequence", 0)),
        id=e.get("id"),
    )


def replay_fixture(
    events: list[dict[str, Any]],
    *,
    compact_replay: bool = True,
    tool_argument_max_chars: int = 1_200,
    tool_result_max_chars: int = 2_400,
    dedup_tool_results: bool = False,
) -> tuple[list, ReplayMetrics]:
    """Replay a fixture and return ``(messages, metrics)``.

    ``events`` is the raw dict stream from a fixture or synthetic generator.
    """
    skip = {"_synthetic_meta"}
    raw = [_dict_to_event(e) for e in events if not (len(e) == 1 and set(e) & skip)]

    messages = _events_to_messages(
        raw,
        compact_replay=compact_replay,
        tool_argument_max_chars=tool_argument_max_chars,
        tool_result_max_chars=tool_result_max_chars,
        dedup_tool_results=dedup_tool_results,
    )
    metrics = compute_replay_metrics(messages)
    return messages, metrics


def replay_jsonl(path: str) -> tuple[list, ReplayMetrics]:
    """Convenience: load a jsonl fixture then :func:`replay_fixture`."""
    from benchmarks.short_memory.dataset import load_fixture

    return replay_fixture(load_fixture(path))


# ---------------------------------------------------------------------------
# CLI helper (used by the future ``benchmark-short-memory`` entry point)
# ---------------------------------------------------------------------------

def format_report(name: str, before: ReplayMetrics, after: ReplayMetrics | None = None) -> str:
    lines = [f"# {name}", ""]
    lines.append(f"  replayed_messages:          {before.replayed_messages}")
    lines.append(f"  body_chars:                 {before.body_chars}")
    lines.append(f"  replayed_tool_result_chars: {before.replayed_tool_result_chars}")
    lines.append(f"  total_tool_results:         {before.total_tool_results}")
    lines.append(f"  deduped_tool_results:       {before.deduped_tool_results}")
    lines.append(f"  dedup_ratio:                {before.dedup_ratio:.4f}")
    if after is not None:
        delta = after.body_chars - before.body_chars
        pct = (delta / before.body_chars * 100) if before.body_chars else 0.0
        lines.append("  -- after change --")
        lines.append(f"  body_chars:                 {after.body_chars}  (delta {delta:+,} / {pct:+.1f}%)")
        lines.append(f"  dedup_ratio:                {after.dedup_ratio:.4f}")
    return "\n".join(lines)
