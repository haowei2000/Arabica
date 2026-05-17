"""Lightweight memory baselines for benchmark harness smoke runs.

These agents do not call an LLM. They approximate the retrieval side of
FullText and NaiveRAG, then return the gold answer only when the selected
context contains it. That keeps CI deterministic while still testing the
baseline context path.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, cast
import unicodedata

from benchmarks.core.types import (
    BenchmarkCase,
    BenchmarkResult,
    CostLedger,
    EvidenceRecord,
)
from benchmarks.longmemeval_v2.release import (
    read_trajectories_by_id,
    trajectory_to_text,
)

_WORDY_PUNCT = re.compile(r"[^0-9a-z一-鿿\s]+")
_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = _WORDY_PUNCT.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def _tokens(text: str) -> set[str]:
    return {token for token in _normalise(text).split() if len(token) > 2}


def _approx_token_count(text: str) -> int:
    return max(1, len(text.split()))


def _reference_strings(reference: object) -> list[str]:
    if reference is None:
        return []
    if isinstance(reference, str):
        return [reference]
    if isinstance(reference, (list, tuple)):
        return [str(item) for item in reference if item is not None]
    if isinstance(reference, dict):
        for key in ("answer", "answers", "reference", "gold"):
            if key in reference:
                return _reference_strings(reference[key])
    return [str(reference)]


def _session_chunk_records(case: BenchmarkCase) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    session_ids = case.inputs.get("haystack_session_ids") or []
    for index, session in enumerate(case.inputs.get("sessions") or [], start=1):
        if not isinstance(session, list):
            continue
        lines: list[str] = []
        for turn in session:
            if isinstance(turn, dict):
                content = turn.get("content") or turn.get("text") or ""
            else:
                content = str(turn)
            if content:
                lines.append(str(content))
        if lines:
            chunk_id = (
                str(session_ids[index - 1])
                if isinstance(session_ids, list) and len(session_ids) >= index
                else f"D{index}"
            )
            chunks.append((chunk_id, "\n".join(lines)))
    return chunks


def _trajectory_chunk_records(case: BenchmarkCase) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    for index, trajectory in enumerate(case.inputs.get("trajectories") or [], start=1):
        if isinstance(trajectory, dict):
            chunk_id = str(
                trajectory.get("id")
                or trajectory.get("trajectory_id")
                or f"trajectory-{index}"
            )
            chunks.append((chunk_id, trajectory_to_text(trajectory)))
    trajectory_ids = case.inputs.get("trajectory_ids") or []
    store_path = case.inputs.get("trajectory_store_path")
    if trajectory_ids and store_path:
        index_path = case.inputs.get("trajectory_offset_index")
        records = read_trajectories_by_id(
            store_path,
            [str(item) for item in trajectory_ids],
            index_path=index_path,
        )
        chunks.extend(
            (
                str(record.get("id") or record.get("trajectory_id") or f"trajectory-{i}"),
                trajectory_to_text(record),
            )
            for i, record in enumerate(records, start=1)
        )
    return chunks


def case_chunk_records(case: BenchmarkCase) -> list[tuple[str, str]]:
    """Return benchmark memory chunks as ``(chunk_id, text)`` pairs."""
    chunks = _session_chunk_records(case)
    if chunks:
        return chunks
    return _trajectory_chunk_records(case)


def _reference_in_context(reference: object, chunks: list[tuple[str, str]]) -> str | None:
    context = _normalise("\n".join(text for _, text in chunks))
    for candidate in _reference_strings(reference):
        normalised = _normalise(candidate)
        if normalised and normalised in context:
            return candidate
    return None


def select_lexical_chunks(
    question: str,
    chunks: list[tuple[str, str]],
    *,
    top_k: int,
) -> list[tuple[str, str]]:
    query_tokens = _tokens(question)
    ranked: list[tuple[int, int, tuple[str, str]]] = []
    for index, chunk in enumerate(chunks):
        overlap = len(query_tokens & _tokens(chunk[1]))
        ranked.append((overlap, -index, chunk))
    ranked.sort(reverse=True)
    selected = [chunk for overlap, _, chunk in ranked[:top_k] if overlap > 0]
    if selected:
        return selected
    return chunks[:top_k]


def evidence_from_selected_context(
    case: BenchmarkCase,
    selected: list[tuple[str, str]],
) -> EvidenceRecord:
    """Classify whether selected context visibly supports the score claim."""
    selected_ids = {chunk_id for chunk_id, _ in selected}
    evidence_ids: list[str] = []
    if isinstance(case.reference, dict):
        raw = case.reference.get("evidence_ids") or case.reference.get("citations")
        if isinstance(raw, str):
            evidence_ids = [raw]
        elif isinstance(raw, list):
            evidence_ids = [str(item) for item in raw if item is not None]
    if not evidence_ids:
        raw = case.metadata.get("evidence")
        if isinstance(raw, str):
            evidence_ids = [raw]
        elif isinstance(raw, list):
            evidence_ids = [str(item) for item in raw if item is not None]
    if evidence_ids:
        matched = [
            item
            for item in evidence_ids
            if item in selected_ids or item.split(":", maxsplit=1)[0] in selected_ids
        ]
        status = "pass" if matched else "unknown"
        return EvidenceRecord(
            status=status,
            artifacts=tuple(matched),
            notes=(
                "selected context contains gold evidence ids"
                if matched
                else "gold evidence ids were not selected"
            ),
        )

    found = _reference_in_context(case.reference, selected)
    if found is not None:
        return EvidenceRecord(
            status="pass",
            artifacts=tuple(chunk_id for chunk_id, _ in selected),
            notes="selected context contains a normalized gold answer string",
        )
    return EvidenceRecord(
        status="unknown",
        artifacts=tuple(chunk_id for chunk_id, _ in selected),
        notes="no explicit evidence ids and gold answer string was not found",
    )


@dataclass
class RetrievalOracleBaseline:
    """Deterministic baseline that answers only when retrieval found evidence."""

    name: Literal["FullText", "NaiveRAG"]
    top_k: int = 3

    async def run(self, case: BenchmarkCase) -> BenchmarkResult:
        chunks = case_chunk_records(case)
        question = str(case.inputs.get("question") or "")
        if self.name == "FullText":
            selected = chunks
        elif self.name == "NaiveRAG":
            selected = select_lexical_chunks(question, chunks, top_k=self.top_k)
        else:
            raise ValueError(f"unsupported baseline: {self.name}")

        answer = _reference_in_context(case.reference, selected) or "unknown"
        prompt = question + "\n" + "\n".join(text for _, text in selected)
        return BenchmarkResult(
            task_id=case.task_id,
            response=answer,
            cost=CostLedger(
                tokens_prompt=_approx_token_count(prompt),
                tokens_completion=_approx_token_count(answer),
                steps=1,
            ),
            metadata={
                "baseline": self.name,
                "selected_chunks": len(selected),
                "available_chunks": len(chunks),
                "oracle_if_retrieved": True,
            },
            evidence=evidence_from_selected_context(case, selected),
        )


def make_memory_baseline(name: str, *, top_k: int = 3) -> RetrievalOracleBaseline:
    """Create a deterministic memory baseline by public method name."""
    if name not in {"FullText", "NaiveRAG"}:
        raise ValueError(f"unsupported memory baseline: {name}")
    baseline_name = cast(Literal["FullText", "NaiveRAG"], name)
    return RetrievalOracleBaseline(name=baseline_name, top_k=top_k)
