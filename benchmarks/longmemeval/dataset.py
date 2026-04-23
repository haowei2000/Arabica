"""Loader for the LongMemEval (Wu et al., ICLR 2025) JSON schema.

The upstream release (https://github.com/xiaowu0162/LongMemEval) ships
each split as a single JSON file whose top level is a list of dicts.
Per-case fields we care about:

    question_id      : str, stable task identifier
    question_type    : str, ability/category tag (see below)
    question         : str, the probe question
    answer           : str | list[str], gold answer(s)
    haystack_sessions: list[list[{"role": str, "content": str}]]
    haystack_session_ids: list[str] (optional)
    answer_session_ids : list[str] (optional, for provenance)

``question_type`` is one of roughly: ``single-session-user``,
``single-session-assistant``, ``single-session-preference``,
``multi-session``, ``temporal-reasoning``, ``knowledge-update``.
We pass it through as :attr:`BenchmarkCase.ability`; the scorer and
aggregator group per-ability automatically.

The loader is tolerant: unknown fields are preserved inside
``BenchmarkCase.metadata`` so per-benchmark code can read them without
touching the core types.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from benchmarks.core.types import BenchmarkCase

REQUIRED_FIELDS = ("question_id", "question", "answer", "haystack_sessions")


def _as_list_of_sessions(raw: Any) -> list[list[dict[str, str]]]:
    if not isinstance(raw, list):
        raise ValueError("haystack_sessions must be a list of sessions")
    sessions: list[list[dict[str, str]]] = []
    for idx, session in enumerate(raw):
        if not isinstance(session, list):
            raise ValueError(f"session {idx} must be a list of turns")
        sessions.append(session)
    return sessions


def _build_case(raw: dict[str, Any]) -> BenchmarkCase:
    for field in REQUIRED_FIELDS:
        if field not in raw:
            raise ValueError(f"missing field {field!r} in LongMemEval case")

    sessions = _as_list_of_sessions(raw["haystack_sessions"])

    inputs = {
        "question": raw["question"],
        "sessions": sessions,
        "haystack_session_ids": raw.get("haystack_session_ids"),
    }
    # Passthrough fields that may or may not be present; kept outside the
    # inputs blob so that the scorer can find them without schema drift.
    metadata = {
        k: v
        for k, v in raw.items()
        if k
        not in {
            "question_id",
            "question",
            "answer",
            "question_type",
            "haystack_sessions",
        }
    }

    return BenchmarkCase(
        task_id=str(raw["question_id"]),
        inputs=inputs,
        reference=raw["answer"],
        ability=raw.get("question_type"),
        metadata=metadata,
    )


def load_longmemeval(path: str | Path) -> list[BenchmarkCase]:
    """Parse a LongMemEval JSON file into a list of :class:`BenchmarkCase`.

    ``path`` accepts any file whose top level is a JSON array of case
    dicts matching the upstream schema (works for the production
    release and for the synthetic fixture used in tests).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: top-level JSON must be a list")
    return [_build_case(entry) for entry in data]


def iter_longmemeval(cases: Iterable[BenchmarkCase]) -> Iterable[BenchmarkCase]:
    """Yield :class:`BenchmarkCase` objects unchanged.

    Thin pass-through kept so callers can swap in streaming loaders or
    shard selectors without touching the runner.
    """
    yield from cases
