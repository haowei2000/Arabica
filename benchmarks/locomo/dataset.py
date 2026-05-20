"""Loader for LoCoMo-style multi-session memory QA data.

The upstream LoCoMo release is conversation-centric: a dialogue has many
sessions and a QA list. This loader accepts that shape and also a flat list
of already-expanded QA cases, then normalises both into ``BenchmarkCase``.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from benchmarks.core.types import BenchmarkCase

SESSION_FIELDS = ("sessions", "haystack_sessions", "conversation", "dialogue")
QA_FIELDS = ("qas", "qa", "question_answering", "questions")
ANSWER_FIELDS = ("answer", "answers", "reference", "gold", "adversarial_answer")
SESSION_KEY = re.compile(r"^session_(\d+)$")


def _read_json_or_jsonl(path: Path) -> Any:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def _normalise_turn(raw: Any) -> dict[str, str]:
    if isinstance(raw, str):
        return {"role": "unknown", "content": raw}
    if not isinstance(raw, dict):
        return {"role": "unknown", "content": str(raw)}
    role = raw.get("role") or raw.get("speaker") or raw.get("from") or "unknown"
    content = raw.get("content") or raw.get("text") or raw.get("utterance") or ""
    return {"role": str(role), "content": str(content)}


def _normalise_sessions(raw: Any) -> list[list[dict[str, str]]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        sessions: list[list[dict[str, str]]] = []
        keys = sorted(
            (
                (int(match.group(1)), key)
                for key in raw
                if (match := SESSION_KEY.match(key))
            ),
            key=lambda item: item[0],
        )
        for _, key in keys:
            value = raw.get(key)
            if isinstance(value, list):
                turns = [_normalise_turn(turn) for turn in value]
                date_value = raw.get(f"{key}_date_time")
                if date_value:
                    turns.insert(
                        0,
                        {
                            "role": "metadata",
                            "content": f"session date: {date_value}",
                        },
                    )
                sessions.append(turns)
        return sessions
    if not isinstance(raw, list):
        raise ValueError("LoCoMo sessions/conversation field must be a list")
    if not raw:
        return []

    first = raw[0]
    if isinstance(first, list):
        return [[_normalise_turn(turn) for turn in session] for session in raw]
    if isinstance(first, dict) and isinstance(first.get("turns"), list):
        return [[_normalise_turn(turn) for turn in session["turns"]] for session in raw]
    return [[_normalise_turn(turn) for turn in raw]]


def _pick_first(raw: dict[str, Any], fields: tuple[str, ...]) -> Any:
    for field in fields:
        if field in raw:
            return raw[field]
    return None


def _conversation_id(raw: dict[str, Any], fallback: int) -> str:
    return str(
        raw.get("conversation_id")
        or raw.get("dialogue_id")
        or raw.get("sample_id")
        or raw.get("id")
        or f"locomo-{fallback:04d}"
    )


def _qa_id(raw: dict[str, Any], fallback: int) -> str:
    return str(raw.get("question_id") or raw.get("qa_id") or raw.get("id") or fallback)


def _question(raw: dict[str, Any]) -> str:
    value = raw.get("question") or raw.get("query") or raw.get("prompt")
    if value is None:
        raise ValueError("LoCoMo QA case is missing a question")
    return str(value)


def _answer(raw: dict[str, Any]) -> Any:
    for field in ANSWER_FIELDS:
        if field in raw:
            return raw[field]
    raise ValueError("LoCoMo QA case is missing an answer/reference")


def _build_case(
    *,
    conversation: dict[str, Any],
    qa: dict[str, Any],
    conversation_index: int,
    qa_index: int,
) -> BenchmarkCase:
    conversation_id = _conversation_id(conversation, conversation_index)
    task_id = f"{conversation_id}:{_qa_id(qa, qa_index)}"
    sessions = _normalise_sessions(_pick_first(conversation, SESSION_FIELDS))
    ability = qa.get("category") or qa.get("question_type") or qa.get("type") or "qa"
    metadata = {
        "conversation_id": conversation_id,
        "qa_index": qa_index,
        "session_count": len(sessions),
    }
    if "evidence" in qa:
        metadata["evidence"] = qa["evidence"]

    return BenchmarkCase(
        task_id=task_id,
        inputs={"question": _question(qa), "sessions": sessions},
        reference=_answer(qa),
        ability=str(ability),
        metadata=metadata,
    )


def _expand_conversation(raw: dict[str, Any], index: int) -> list[BenchmarkCase]:
    qas = _pick_first(raw, QA_FIELDS)
    if qas is None and "question" in raw:
        qas = [raw]
    if not isinstance(qas, list):
        raise ValueError("LoCoMo QA field must be a list")
    return [
        _build_case(
            conversation=raw,
            qa=qa,
            conversation_index=index,
            qa_index=qa_index,
        )
        for qa_index, qa in enumerate(qas)
        if isinstance(qa, dict)
    ]


def load_locomo(path: str | Path) -> list[BenchmarkCase]:
    """Parse a LoCoMo JSON/JSONL file into benchmark cases."""
    data = _read_json_or_jsonl(Path(path))
    if isinstance(data, dict):
        data = data.get("data") or data.get("conversations") or [data]
    if not isinstance(data, list):
        raise ValueError(f"{path}: top-level LoCoMo data must be a list or dict")

    cases: list[BenchmarkCase] = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: LoCoMo entry {index} must be a dict")
        cases.extend(_expand_conversation(entry, index))
    return cases
