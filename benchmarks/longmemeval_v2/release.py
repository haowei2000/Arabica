"""Utilities for the public LongMemEval-V2 release layout.

The upstream release stores questions, haystack mappings, and trajectories
separately.  Keeping trajectories out of each ``BenchmarkCase`` avoids
duplicating the 1GB+ JSONL file in memory.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any
import unicodedata

from benchmarks.core.types import BenchmarkCase

_WORDY_PUNCT = re.compile(r"[^0-9a-z一-鿿\s]+")
_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = _WORDY_PUNCT.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def _tokens(text: str) -> set[str]:
    return {token for token in _normalise(text).split() if len(token) > 2}


def build_trajectory_offset_index(
    trajectories_path: str | Path,
    index_path: str | Path | None = None,
) -> Path:
    """Create a byte-offset index for ``trajectories.jsonl``."""
    trajectories = Path(trajectories_path)
    index = (
        Path(index_path) if index_path else trajectories.with_suffix(".offsets.json")
    )

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
    index = (
        Path(index_path) if index_path else trajectories.with_suffix(".offsets.json")
    )
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


def trajectory_evidence_id(trajectory_id: str, state: dict[str, Any]) -> str:
    """Return a stable citation id for one trajectory state."""
    state_index = state.get("state_index", state.get("step", ""))
    if state_index == "":
        state_index = "unknown"
    return f"{trajectory_id}:s{state_index}"


def trajectory_state_to_text(
    record: dict[str, Any],
    state: dict[str, Any],
    *,
    max_state_chars: int = 2000,
) -> str:
    """Render one trajectory state with citation and screenshot metadata."""
    trajectory_id = str(record.get("id") or record.get("trajectory_id") or "unknown")
    state_index = state.get("state_index", state.get("step", ""))
    action = state.get("action")
    thought = state.get("thought")
    observation = (
        state.get("accessibility_tree")
        or state.get("observation")
        or state.get("text")
        or ""
    )
    screenshot = state.get("screenshot")
    url = state.get("url")
    parts = [
        f"evidence_id: {trajectory_evidence_id(trajectory_id, state)}",
        f"state: {state_index}",
    ]
    if url:
        parts.append(f"url: {url}")
    if screenshot:
        parts.append(f"screenshot: {screenshot}")
    if action:
        parts.append(f"action: {action}")
    if thought:
        parts.append(f"thought: {thought}")
    if observation:
        parts.append(f"observation: {str(observation)[:max_state_chars]}")
    return "\n".join(parts)


def _state_relevance(question: str, state: dict[str, Any]) -> int:
    query_tokens = _tokens(question)
    if not query_tokens:
        return 0
    haystack = " ".join(
        str(state.get(key) or "")
        for key in (
            "url",
            "action",
            "thought",
            "accessibility_tree",
            "observation",
            "text",
        )
    )
    return len(query_tokens & _tokens(haystack))


def extract_trajectory_evidence(
    record: dict[str, Any],
    *,
    question: str = "",
    max_states: int = 8,
    max_state_chars: int = 2000,
) -> list[tuple[str, str]]:
    """Return compact state-level evidence snippets for a trajectory.

    The helper ranks states by lexical overlap with the question and keeps the
    selected snippets in original trajectory order.  It is intentionally
    deterministic so fixed benchmark samples remain reproducible.
    """
    trajectory_id = str(record.get("id") or record.get("trajectory_id") or "unknown")
    states = record.get("states") or record.get("events") or []
    if not isinstance(states, list):
        return []

    dict_states = [state for state in states if isinstance(state, dict)]
    if max_states > 0 and len(dict_states) > max_states:
        ranked = [
            (_state_relevance(question, state), -index, index, state)
            for index, state in enumerate(dict_states)
        ]
        ranked.sort(reverse=True)
        selected_indices = {
            index for score, _, index, _ in ranked[:max_states] if score > 0
        }
        if not selected_indices:
            selected_indices = set(range(max_states))
        dict_states = [
            state
            for index, state in enumerate(dict_states)
            if index in selected_indices
        ]

    return [
        (
            trajectory_evidence_id(trajectory_id, state),
            trajectory_state_to_text(
                record,
                state,
                max_state_chars=max_state_chars,
            ),
        )
        for state in dict_states
    ]


def trajectory_to_text(
    record: dict[str, Any],
    *,
    max_state_chars: int = 2000,
    question: str = "",
    max_states: int | None = None,
) -> str:
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
        if max_states is not None:
            evidence_records = extract_trajectory_evidence(
                record,
                question=question,
                max_states=max_states,
                max_state_chars=max_state_chars,
            )
            parts.extend(text for _, text in evidence_records)
            return "\n".join(part for part in parts if part)

        for index, state in enumerate(states):
            if not isinstance(state, dict):
                parts.append(str(state)[:max_state_chars])
                continue
            if "state_index" not in state and "step" not in state:
                state = {**state, "state_index": index}
            parts.append(
                trajectory_state_to_text(record, state, max_state_chars=max_state_chars)
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
