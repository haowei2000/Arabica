"""Utilities for the public LongMemEval-V2 release layout.

The upstream release stores questions, haystack mappings, and trajectories
separately.  Keeping trajectories out of each ``BenchmarkCase`` avoids
duplicating the 1GB+ JSONL file in memory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmarks.core.types import BenchmarkCase


def build_trajectory_offset_index(
    trajectories_path: str | Path,
    index_path: str | Path | None = None,
) -> Path:
    """Create a byte-offset index for ``trajectories.jsonl``."""
    trajectories = Path(trajectories_path)
    index = Path(index_path) if index_path else trajectories.with_suffix(".offsets.json")

    offsets: dict[str, int] = {}
    with trajectories.open("rb") as handle:
        while True:
            offset = handle.tell()
            line = handle.readline()
            if not line:
                break
            if not line.strip():
                continue
            record = json.loads(line)
            trajectory_id = record.get("id") or record.get("trajectory_id")
            if trajectory_id is not None:
                offsets[str(trajectory_id)] = offset

    index.write_text(json.dumps(offsets, indent=2, sort_keys=True), encoding="utf-8")
    return index


def _load_offsets(index_path: Path) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in json.loads(index_path.read_text(encoding="utf-8")).items()
    }


def read_trajectories_by_id(
    trajectories_path: str | Path,
    trajectory_ids: list[str],
    *,
    index_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Read selected trajectory records using a precomputed offset index."""
    trajectories = Path(trajectories_path)
    index = Path(index_path) if index_path else trajectories.with_suffix(".offsets.json")
    if not index.exists():
        build_trajectory_offset_index(trajectories, index)

    offsets = _load_offsets(index)
    records: list[dict[str, Any]] = []
    with trajectories.open("rb") as handle:
        for trajectory_id in trajectory_ids:
            offset = offsets.get(str(trajectory_id))
            if offset is None:
                continue
            handle.seek(offset)
            records.append(json.loads(handle.readline()))
    return records


def trajectory_to_text(record: dict[str, Any], *, max_state_chars: int = 2000) -> str:
    """Render a trajectory into text suitable for retrieval and reader prompts."""
    trajectory_id = str(record.get("id") or record.get("trajectory_id") or "unknown")
    parts = [
        f"[Trajectory {trajectory_id}]",
        f"domain: {record.get('domain', '')}",
        f"environment: {record.get('environment', '')}",
        f"goal: {record.get('goal', '')}",
        f"outcome: {record.get('outcome', '')}",
    ]
    states = record.get("states") or record.get("events") or []
    if isinstance(states, list):
        for state in states:
            if not isinstance(state, dict):
                parts.append(str(state)[:max_state_chars])
                continue
            state_index = state.get("state_index", state.get("step", ""))
            action = state.get("action")
            thought = state.get("thought")
            observation = (
                state.get("accessibility_tree")
                or state.get("observation")
                or state.get("text")
                or ""
            )
            parts.append(
                "\n".join(
                    [
                        f"state: {state_index}",
                        f"action: {action}",
                        f"thought: {thought}",
                        f"observation: {str(observation)[:max_state_chars]}",
                    ]
                )
            )
    return "\n".join(part for part in parts if part)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_longmemeval_v2_release(
    root: str | Path,
    *,
    split: str = "small",
) -> list[BenchmarkCase]:
    """Load the public release as lazy trajectory cases.

    ``split`` accepts ``small`` or ``medium`` and maps to the corresponding
    haystack file under ``haystacks/``.
    """
    root_path = Path(root)
    split_name = split.removeprefix("lme_v2_")
    questions_path = root_path / "questions.jsonl"
    haystack_path = root_path / "haystacks" / f"lme_v2_{split_name}.json"
    trajectories_path = root_path / "trajectories.jsonl"

    questions = {str(row["id"]): row for row in _read_jsonl(questions_path)}
    haystacks = json.loads(haystack_path.read_text(encoding="utf-8"))

    cases: list[BenchmarkCase] = []
    for question_id, trajectory_ids in haystacks.items():
        row = questions.get(str(question_id))
        if row is None:
            continue
        answer = row.get("answer")
        cases.append(
            BenchmarkCase(
                task_id=str(question_id),
                inputs={
                    "question": row["question"],
                    "trajectory_ids": [str(item) for item in trajectory_ids],
                    "trajectory_store_path": str(trajectories_path),
                    "trajectory_offset_index": str(
                        trajectories_path.with_suffix(".offsets.json")
                    ),
                    "memory_protocol": "insert_trajectories_then_query",
                },
                reference={
                    "answer": answer,
                    "evidence_ids": [],
                    "eval_function": row.get("eval_function"),
                },
                ability=row.get("question_type"),
                metadata={
                    "domain": row.get("domain"),
                    "environment": row.get("environment"),
                    "image": row.get("image"),
                    "split": f"lme_v2_{split_name}",
                    "trajectory_count": len(trajectory_ids),
                    "public_release_has_evidence_ids": False,
                },
            )
        )
    return cases
