"""Deterministic scorer for LongMemEval-V2 smoke fixtures."""

from __future__ import annotations

from benchmarks.longmemeval.scorer import _normalise

ANSWER_WEIGHT = 0.8
EVIDENCE_WEIGHT = 0.2


def _as_strings(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple, set)):
        return [str(item) for item in raw if item is not None]
    return [str(raw)]


def _extract_answer(raw: object) -> object:
    if isinstance(raw, dict):
        return raw.get("answer", raw.get("answers", raw.get("response")))
    return raw


def _extract_evidence_ids(raw: object) -> list[str]:
    if isinstance(raw, dict):
        for key in ("evidence_ids", "citations", "evidence"):
            if key in raw:
                return _as_strings(raw[key])
    return []


def _answer_match(reference: object, response: object) -> float:
    gold = [_normalise(item) for item in _as_strings(_extract_answer(reference))]
    pred = _normalise(" ".join(_as_strings(_extract_answer(response))))
    if not gold or not pred:
        return 0.0
    return 1.0 if any(item and item in pred for item in gold) else 0.0


def _evidence_match(reference: object, response: object) -> float:
    gold = set(_extract_evidence_ids(reference))
    if not gold:
        return 1.0

    response_ids = set(_extract_evidence_ids(response))
    if not response_ids and response is not None:
        response_text = _normalise(str(response))
        response_ids = {
            item
            for item in gold
            if _normalise(item) and _normalise(item) in response_text
        }
    if not response_ids:
        return 0.0
    return len(gold & response_ids) / len(gold)


def longmemeval_v2_scorer(reference: object, response: object) -> float:
    """Blend answer accuracy with evidence-id recall.

    The fixture uses deterministic substring matching so CI can validate
    adapter wiring without an LLM judge. When evidence ids are present,
    the score is 80% answer match and 20% evidence recall; otherwise it
    falls back to answer-only matching.
    """
    answer_score = _answer_match(reference, response)
    if not _extract_evidence_ids(reference):
        return answer_score
    return ANSWER_WEIGHT * answer_score + EVIDENCE_WEIGHT * _evidence_match(
        reference,
        response,
    )
