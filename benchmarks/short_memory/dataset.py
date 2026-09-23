"""Fixtures + synthetic generators for the short-memory benchmark.

Two input styles, both emitting the same ``list[Event]``-ish dict stream that
``_events_to_messages`` already consumes:

1. load_fixture(path)          — load a hand-authored / exported jsonl fixture.
2. generate_redundant_log(...)  — synthesize a log with a *known* redundancy so
   the benchmark has a ground-truth dedup_ratio to assert against.
"""

from __future__ import annotations

from collections.abc import Iterable
import json
from pathlib import Path
from typing import Any

from structure.core.enums.events import EventType

# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------

def load_fixture(path: str) -> list[dict[str, Any]]:
    """Load an event-log fixture (one JSON object per line).

    Each line must at minimum carry ``event_type`` and ``sequence``.  The
    fields used by ``_events_to_messages`` are ``event_type``, ``payload``,
    ``sequence``, and ``id`` (all others pass through harmlessly).
    """
    events: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:  # pragma: no cover - debug helper
                raise ValueError(f"{path}:{lineno}: invalid json ({exc})") from exc
            events.append(obj)
    return events


# ---------------------------------------------------------------------------
# Synthetic generator
# ---------------------------------------------------------------------------

def _user_turn(seq: int, text: str) -> dict[str, Any]:
    return {
        "sequence": seq,
        "event_type": str(EventType.USER_MESSAGE),
        "payload": {"message": text},
    }


def _agent_turn(seq: int, content: str, tool_calls: list[dict] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"content": content}
    if tool_calls:
        payload["tool_calls"] = tool_calls
    return {
        "sequence": seq,
        "event_type": str(EventType.AGENT_MESSAGE),
        "payload": payload,
    }


def _tool_result(seq: int, tool_call_id: str, tool_name: str, result: Any) -> dict[str, Any]:
    return {
        "sequence": seq,
        "event_type": str(EventType.TOOL_RESULT),
        "payload": {
            "tool_id": tool_call_id,
            "tool_name": tool_name,
            "result": result,
        },
    }


def _tool_error(seq: int, tool_call_id: str, tool_name: str, error: str) -> dict[str, Any]:
    return {
        "sequence": seq,
        "event_type": str(EventType.TOOL_ERROR),
        "payload": {
            "tool_id": tool_call_id,
            "tool_name": tool_name,
            "error_message": error,
        },
    }


def generate_redundant_log(
    n_turns: int = 20,
    repeat_ratio: float = 0.4,
    repeated_paths: Iterable[str] = ("/knowledge/api_spec",),
    result_size_chars: int = 1500,
    seed: int = 20260720,
) -> list[dict[str, Any]]:
    """Build a deterministic event log with a *known* dedup target.

    The function emits ``n_turns`` user/agent/tool triplets.  Each turn either

    - reads a **fresh** unique path (``/knowledge/unique_{turn>0}``) whose body
      is turn-specific so it is never a duplicate, or
    - re-reads one of ``repeated_paths`` that an earlier turn already read.

    ``repeat_ratio`` is the fraction of turns that are *forced* to repeat an
    earlier read.  Because the repeated read returns identical result text to
    the first occurrence of that path, it *should* be caught by a dedup pass.

    Returns the log with a trailing ``_synthetic_meta`` row carrying
    ``expected_dedup_ratio`` (see :func:`expected_dedup_ratio`).

    Ground-truth invariant::

        expected_dedup_ratio == duplicate_reads / total_tool_reads
    """
    import hashlib

    repeated_paths_list = list(repeated_paths)
    rng = _DeterministicRng(seed)
    events: list[dict[str, Any]] = []
    seq = 1

    # Stable body per *path* (not per turn): every read of the same path returns
    # the same body, so repeated reads of /knowledge/api_spec always match.
    path_bodies: dict[str, str] = {}
    total_tool_reads = 0
    duplicate_reads = 0

    def _body_for(path: str) -> str:
        if path not in path_bodies:
            path_bodies[path] = (
                "x" * (result_size_chars - 40) + hashlib.sha256(path.encode()).hexdigest() + hashlib.sha256(str(seed).encode()).hexdigest()[:24]
            )
        return path_bodies[path]

    for turn in range(n_turns):
        events.append(_user_turn(seq, f"turn {turn + 1}: please review the relevant docs"))
        seq += 1

        # Decide: re-read one of the already-known repeated paths, or read a
        # brand-new turn-specific path.
        if rng.pick_bool(repeat_ratio) and path_bodies:
            path = rng.choice(sorted(path_bodies.keys()))
            duplicate_reads += 1
        else:
            path = f"/knowledge/unique_{turn}"
            if turn == 0 and repeated_paths_list:
                # Always seed at least one occurrence of the first repeated path
                # so the pool is non-empty for the repeat branch to pick from.
                path = repeated_paths_list[0]

        tool_call_id = f"tc_{turn}"
        events.append(
            _agent_turn(
                seq,
                f"let me read {path}",
                tool_calls=[{"id": tool_call_id, "name": "read_context", "arguments": {"path": path}}],
            )
        )
        seq += 1

        result_text = f"content of {path}: {_body_for(path)}"
        events.append(
            _tool_result(seq, tool_call_id, "read_context", {"path": path, "content": result_text})
        )
        seq += 1
        total_tool_reads += 1

    events.append({"_synthetic_meta": {
        "n_turns": n_turns,
        "total_tool_reads": total_tool_reads,
        "duplicate_reads": duplicate_reads,
        "expected_dedup_ratio": (duplicate_reads / total_tool_reads) if total_tool_reads else 0.0,
    }})
    return events


def expected_dedup_ratio(log: list[dict[str, Any]]) -> float:
    """Return the ``expected_dedup_ratio`` stored by ``generate_redundant_log``."""
    for e in reversed(log):
        if "_synthetic_meta" in e:
            return float(e["_synthetic_meta"]["expected_dedup_ratio"])
    return 0.0


def strip_meta(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the trailing ``_synthetic_meta`` row before replaying."""
    if log and "_synthetic_meta" in log[-1]:
        return log[:-1]
    return log


# ---------------------------------------------------------------------------
# Tiny deterministic RNG (no random module dependency -> reproducible CI)
# ---------------------------------------------------------------------------

class _DeterministicRng:
    def __init__(self, seed: int) -> None:
        self._state = seed & 0xFFFFFFFFFFFFFFFF

    def _next(self) -> int:
        # xorshift64*
        x = self._state
        x ^= (x >> 12) & 0xFFFFFFFFFFFFFFFF
        x ^= (x << 25) & 0xFFFFFFFFFFFFFFFF
        x ^= (x >> 27) & 0xFFFFFFFFFFFFFFFF
        self._state = x & 0xFFFFFFFFFFFFFFFF
        return (x * 0x2545F4914F6CDD1D) & 0xFFFFFFFFFFFFFFFF

    def pick_bool(self, probability: float) -> bool:
        return (self._next() % 10_000) / 10_000 < probability

    def choice(self, items: list[str]) -> str:
        return items[self._next() % len(items)]
