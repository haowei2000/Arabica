"""Deterministic QA scorer for the LoCoMo adapter."""

from __future__ import annotations

import re
import unicodedata

_WORDY_PUNCT = re.compile(r"[^0-9a-z一-鿿\s]+")
_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = _WORDY_PUNCT.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def _as_strings(reference: object) -> list[str]:
    if reference is None:
        return []
    if isinstance(reference, str):
        return [reference]
    if isinstance(reference, (list, tuple)):
        return [str(item) for item in reference if item is not None]
    if isinstance(reference, dict):
        for key in ("answer", "answers", "reference", "gold"):
            if key in reference:
                return _as_strings(reference[key])
    return [str(reference)]


def _token_f1(gold: str, pred: str) -> float:
    gold_tokens = gold.split()
    pred_tokens = pred.split()
    if not gold_tokens or not pred_tokens:
        return 0.0

    remaining = pred_tokens.copy()
    overlap = 0
    for token in gold_tokens:
        if token in remaining:
            overlap += 1
            remaining.remove(token)
    if overlap == 0:
        return 0.0

    precision = overlap / len(pred_tokens)
    recall = overlap / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def locomo_qa_scorer(reference: object, response: object) -> float:
    """Score LoCoMo QA with exact/substring match, falling back to token F1."""
    if response is None:
        return 0.0

    pred = _normalise(str(response))
    if not pred:
        return 0.0

    scores: list[float] = []
    for candidate in _as_strings(reference):
        gold = _normalise(candidate)
        if not gold:
            continue
        if gold in pred:
            return 1.0
        scores.append(_token_f1(gold, pred))
    return max(scores, default=0.0)

