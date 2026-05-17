"""Scorers for LongMemEval-V2.

The public LongMemEval-V2 release stores a per-question ``eval_function``
string.  This module implements the deterministic functions that appear in
the release and keeps a conservative fallback for the LLM-judge functions so
local benchmark runs remain reproducible without a second judge model.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import string
import unicodedata

from benchmarks.longmemeval.scorer import _normalise

ANSWER_WEIGHT = 0.8
EVIDENCE_WEIGHT = 0.2
LLM_JUDGE_FUNCTIONS = {"llm_abstention_checker", "llm_gotchas_checker"}

_EVIDENCE_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_/-])([A-Za-z0-9][A-Za-z0-9._/-]*:(?:s|e|state-?)\d+)"
)
_FENCED_JSON_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
_HYPHENS = "\u2010\u2011\u2012\u2013\u2014\u2212-"
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class EvalFunctionSpec:
    """Parsed representation of a LongMemEval-V2 eval function string."""

    name: str
    options: dict[str, str]


def parse_eval_function(raw: object) -> EvalFunctionSpec | None:
    """Parse ``name|key=value`` evaluator strings from the public release."""
    if not isinstance(raw, str) or not raw.strip():
        return None

    parts = [part.strip() for part in raw.split("|") if part.strip()]
    if not parts:
        return None

    options: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            options[part] = "true"
            continue
        key, value = part.split("=", maxsplit=1)
        options[key.strip()] = value.strip()
    return EvalFunctionSpec(name=parts[0], options=options)


def _as_strings(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple, set)):
        return [str(item) for item in raw if item is not None]
    return [str(raw)]


def _decode_json_payload(raw: object) -> object:
    """Decode plain or fenced JSON responses when possible."""
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    match = _FENCED_JSON_RE.match(text)
    if match:
        text = match.group(1).strip()
    if not text.startswith(("{", "[")):
        return raw
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return raw


def _extract_answer(raw: object) -> object:
    raw = _decode_json_payload(raw)
    if isinstance(raw, dict):
        return raw.get("answer", raw.get("answers", raw.get("response")))
    return raw


def _extract_evidence_ids(raw: object) -> list[str]:
    raw = _decode_json_payload(raw)
    if isinstance(raw, dict):
        for key in ("evidence_ids", "citations", "evidence"):
            if key in raw:
                return _as_strings(raw[key])
    if isinstance(raw, str):
        return [match.group(1) for match in _EVIDENCE_ID_RE.finditer(raw)]
    return []


def extract_evidence_ids(raw: object) -> list[str]:
    """Public evidence-id extraction used by evidence audit helpers."""
    return _extract_evidence_ids(raw)


def _answer_match(reference: object, response: object) -> float:
    gold = [_normalise(item) for item in _as_strings(_extract_answer(reference))]
    pred = _normalise(" ".join(_as_strings(_extract_answer(response))))
    if not gold or not pred:
        return 0.0
    return 1.0 if any(item and item in pred for item in gold) else 0.0


def _option_bool(options: dict[str, str], key: str) -> bool:
    return options.get(key, "false").lower() in {"1", "true", "yes", "y"}


def _normalise_eval_text(text: str, spec: EvalFunctionSpec) -> str:
    options = spec.options
    text = unicodedata.normalize("NFKC", text)
    if _option_bool(options, "lower"):
        text = text.lower()
    if _option_bool(options, "normalize_hyphen"):
        text = text.translate(str.maketrans(dict.fromkeys(_HYPHENS, " ")))
    if _option_bool(options, "strip_punct"):
        text = "".join(
            " " if unicodedata.category(char).startswith(("P", "S")) else char
            for char in text
        )
    return _WHITESPACE.sub(" ", text).strip()


def _split_phrases(raw: object, spec: EvalFunctionSpec) -> list[str]:
    separators = spec.options.get("separators", "")
    phrases: list[str] = []
    for text in _as_strings(_extract_answer(raw)):
        parts = re.split(f"[{re.escape(separators)}]", text) if separators else [text]
        phrases.extend(_normalise_eval_text(part, spec) for part in parts)

    if _option_bool(spec.options, "require_non_empty"):
        phrases = [phrase for phrase in phrases if phrase]
    return phrases


def _phrase_set_score(
    reference: object,
    response: object,
    spec: EvalFunctionSpec,
    *,
    ordered: bool,
) -> float:
    gold = _split_phrases(reference, spec)
    pred = _split_phrases(response, spec)
    if _option_bool(spec.options, "require_non_empty") and (not gold or not pred):
        return 0.0
    if ordered:
        return 1.0 if gold == pred else 0.0
    return 1.0 if set(gold) == set(pred) else 0.0


def _choice_tokens(raw: object) -> list[str]:
    answer = _extract_answer(raw)
    if isinstance(answer, (list, tuple, set)):
        text = ",".join(str(item) for item in answer if item is not None)
    else:
        text = str(answer or "")
    text = unicodedata.normalize("NFKC", text).upper()
    text = text.translate(str.maketrans(dict.fromkeys(string.punctuation, " ")))
    return re.findall(r"\b[A-Z]\b", text)


def _mc_choice_score(reference: object, response: object) -> float:
    gold = _choice_tokens(reference)
    pred = _choice_tokens(response)
    if not gold or not pred:
        return 0.0
    return 1.0 if gold[0] == pred[0] else 0.0


def _mc_choice_set_score(reference: object, response: object) -> float:
    gold = set(_choice_tokens(reference))
    pred = set(_choice_tokens(response))
    if not gold or not pred:
        return 0.0
    return 1.0 if gold == pred else 0.0


def score_with_eval_function(reference: object, response: object) -> float:
    """Score an answer using the official per-question evaluator string.

    LLM evaluator names are recognised, but this deterministic runner does
    not call a second judge model.  For those functions the scorer falls back
    to normalized answer containment, which should be interpreted as a local
    approximation rather than a full official reproduction.
    """
    eval_function = None
    if isinstance(reference, dict):
        eval_function = reference.get("eval_function")
    spec = parse_eval_function(eval_function)
    if spec is None:
        return _answer_match(reference, response)

    if spec.name == "norm_phrase_set_match":
        return _phrase_set_score(reference, response, spec, ordered=False)
    if spec.name == "norm_phrase_set_match_ordered":
        return _phrase_set_score(reference, response, spec, ordered=True)
    if spec.name == "mc_choice_match":
        return _mc_choice_score(reference, response)
    if spec.name == "mc_choice_set_match":
        return _mc_choice_set_score(reference, response)
    if spec.name in LLM_JUDGE_FUNCTIONS:
        return _answer_match(reference, response)
    return _answer_match(reference, response)


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
    """Blend official answer scoring with evidence-id recall.

    When a release row includes ``eval_function``, the answer component uses
    that evaluator. When evidence ids are present, the score is 80% answer
    match and 20% evidence recall; otherwise it reports answer-only scoring.
    """
    answer_score = score_with_eval_function(reference, response)
    if not _extract_evidence_ids(reference):
        return answer_score
    return ANSWER_WEIGHT * answer_score + EVIDENCE_WEIGHT * _evidence_match(
        reference,
        response,
    )
