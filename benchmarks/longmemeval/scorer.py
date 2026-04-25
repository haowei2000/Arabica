"""Scorer for LongMemEval.

The upstream paper recommends an LLM judge for the open-ended subset;
here we implement the deterministic exact-/substring-match variant the
release ships in ``evaluation/match_eval.py`` so the harness can score
results in CI without an LLM round-trip.  An LLM-judge scorer with the
same signature can be dropped in later.
"""

from __future__ import annotations

import re
import unicodedata

_WORDY_PUNCT = re.compile(r"[^0-9a-z一-鿿\s]+")
_WHITESPACE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """Lowercase, NFKC-normalise, drop punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = _WORDY_PUNCT.sub(" ", text)
    text = _WHITESPACE.sub(" ", text)
    return text.strip()


def _to_string_list(reference: object) -> list[str]:
    """Accept a str, a list of str, or a list mixing the two."""
    if reference is None:
        return []
    if isinstance(reference, str):
        return [reference]
    if isinstance(reference, (list, tuple)):
        out: list[str] = []
        for item in reference:
            if isinstance(item, str):
                out.append(item)
            elif item is not None:
                out.append(str(item))
        return out
    return [str(reference)]


def longmemeval_scorer(reference: object, response: object) -> float:
    """Return 1.0 if any gold string is a substring of the response.

    LongMemEval allows multiple acceptable answers per question; the
    upstream exact-match variant credits the model if it produces any
    one of them.  Responses and references are normalised (lowercased,
    punctuation stripped, whitespace collapsed) so minor formatting
    differences do not wrongly count as misses.
    """
    if response is None:
        return 0.0

    gold = [_normalise(s) for s in _to_string_list(reference) if s]
    if not gold:
        # No gold --- treat as abstain, score 0 so callers notice.
        return 0.0

    pred = _normalise(str(response))
    if not pred:
        return 0.0

    for candidate in gold:
        if candidate and candidate in pred:
            return 1.0
    return 0.0
