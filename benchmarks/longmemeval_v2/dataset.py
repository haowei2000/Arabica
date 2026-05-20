"""Loader for the LongMemEval-V2 memory-system benchmark schema.

The upstream benchmark evaluates an explicit memory interface: history
trajectories are inserted into a memory system, then later questions
query that memory and expect both an answer and supporting evidence.
This loader keeps that Insert/Query shape visible in ``inputs`` while
remaining tolerant of minor schema variants that may appear in the full
release.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmarks.core.types import BenchmarkCase


def _first_present(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in raw:
            return raw[key]
    return None


def _normalise_trajectories(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("trajectories must be a list")

    trajectories: list[dict[str, Any]] = []
    for idx, trajectory in enumerate(raw):
        if isinstance(trajectory, dict):
            events = trajectory.get("events", [])
            if not isinstance(events, list):
                raise ValueError(f"trajectory {idx} events must be a list")
            trajectories.append(trajectory)
            continue
        if isinstance(trajectory, list):
            trajectories.append(
                {
                    "trajectory_id": f"trajectory-{idx + 1}",
                    "events": trajectory,
                }
            )
            continue
        raise ValueError(f"trajectory {idx} must be an object or list of events")
    return trajectories


def _to_string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw if item is not None]
    return [str(raw)]


def _build_case(raw: dict[str, Any]) -> BenchmarkCase:
    task_id = _first_present(raw, "question_id", "task_id", "id")
    question = _first_present(raw, "question", "query")
    answer = _first_present(raw, "answer", "answers", "reference_answer")
    trajectories_raw = _first_present(
        raw,
        "trajectories",
        "history_trajectories",
        "history",
    )

    if task_id is None:
        raise ValueError("missing question_id/task_id/id in LongMemEval-V2 case")
    if question is None:
        raise ValueError(f"{task_id}: missing question/query")
    if answer is None:
        raise ValueError(f"{task_id}: missing answer/reference_answer")
    if trajectories_raw is None:
        raise ValueError(f"{task_id}: missing trajectories/history")

    trajectories = _normalise_trajectories(trajectories_raw)
    evidence_ids = _to_string_list(
        _first_present(raw, "evidence_ids", "answer_evidence_ids", "citations")
    )

    inputs = {
        "question": question,
        "trajectories": trajectories,
        "memory_protocol": "insert_trajectories_then_query",
        "evidence_ids": evidence_ids,
    }
    reference = {
        "answer": answer,
        "evidence_ids": evidence_ids,
    }
    metadata = {
        key: value
        for key, value in raw.items()
        if key
        not in {
            "question_id",
            "task_id",
            "id",
            "question",
            "query",
            "answer",
            "answers",
            "reference_answer",
            "trajectories",
            "history_trajectories",
            "history",
            "question_type",
            "ability",
            "evidence_ids",
            "answer_evidence_ids",
            "citations",
        }
    }

    return BenchmarkCase(
        task_id=str(task_id),
        inputs=inputs,
        reference=reference,
        ability=_first_present(raw, "question_type", "ability"),
        metadata=metadata,
    )


def load_longmemeval_v2(path: str | Path) -> list[BenchmarkCase]:
    """Parse a LongMemEval-V2 JSON fixture into benchmark cases."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: top-level JSON must be a list")
    return [_build_case(entry) for entry in data]
