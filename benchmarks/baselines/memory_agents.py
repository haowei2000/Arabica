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

from benchmarks.core.types import BenchmarkCase, BenchmarkResult, CostLedger

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


def _session_chunks(case: BenchmarkCase) -> list[str]:
    chunks: list[str] = []
    for session in case.inputs.get("sessions") or []:
        if not isinstance(session, list):
            continue
        for turn in session:
            if isinstance(turn, dict):
                content = turn.get("content") or turn.get("text") or ""
            else:
                content = str(turn)
            if content:
                chunks.append(str(content))
    return chunks


def _reference_in_context(reference: object, chunks: list[str]) -> str | None:
    context = _normalise("\n".join(chunks))
    for candidate in _reference_strings(reference):
        normalised = _normalise(candidate)
        if normalised and normalised in context:
            return candidate
    return None


def _select_lexical_chunks(
    question: str,
    chunks: list[str],
    *,
    top_k: int,
) -> list[str]:
    query_tokens = _tokens(question)
    ranked: list[tuple[int, int, str]] = []
    for index, chunk in enumerate(chunks):
        overlap = len(query_tokens & _tokens(chunk))
        ranked.append((overlap, -index, chunk))
    ranked.sort(reverse=True)
    selected = [chunk for overlap, _, chunk in ranked[:top_k] if overlap > 0]
    if selected:
        return selected
    return chunks[:top_k]


@dataclass
class RetrievalOracleBaseline:
    """Deterministic baseline that answers only when retrieval found evidence."""

    name: Literal["FullText", "NaiveRAG"]
    top_k: int = 3

    async def run(self, case: BenchmarkCase) -> BenchmarkResult:
        chunks = _session_chunks(case)
        question = str(case.inputs.get("question") or "")
        if self.name == "FullText":
            selected = chunks
        elif self.name == "NaiveRAG":
            selected = _select_lexical_chunks(question, chunks, top_k=self.top_k)
        else:
            raise ValueError(f"unsupported baseline: {self.name}")

        answer = _reference_in_context(case.reference, selected) or "unknown"
        prompt = question + "\n" + "\n".join(selected)
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
        )


def make_memory_baseline(name: str, *, top_k: int = 3) -> RetrievalOracleBaseline:
    """Create a deterministic memory baseline by public method name."""
    if name not in {"FullText", "NaiveRAG"}:
        raise ValueError(f"unsupported memory baseline: {name}")
    baseline_name = cast(Literal["FullText", "NaiveRAG"], name)
    return RetrievalOracleBaseline(name=baseline_name, top_k=top_k)
