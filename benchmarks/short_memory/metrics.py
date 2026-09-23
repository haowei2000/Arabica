"""Three-dimensional metrics for short-memory benchmarking.

Shared contract between the pure-function regression tests (this module) and the
paper's ablation table.  Units are deliberately *chars* not *tokens* so the
metrics are tokenizer-independent and cheap to compute (no tiktoken / no API
call).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import hashlib

from benchmarks.core.types import CostLedger

# ---------------------------------------------------------------------------
# Per-replay measurements
# ---------------------------------------------------------------------------

@dataclass
class ReplayMetrics:
    body_chars: int = 0
    head_chars: int = 0
    replayed_messages: int = 0
    replayed_tool_result_chars: int = 0
    unique_tool_keys: int = 0
    deduped_tool_results: int = 0  # how many tool-result blocks were folded
    total_tool_results: int = 0

    @property
    def dedup_ratio(self) -> float:
        return self.deduped_tool_results / self.total_tool_results if self.total_tool_results else 0.0

    @property
    def estimated_prompt_tokens(self) -> int:
        """Very rough token proxy: ~4 chars/token for mixed EN+code."""
        return (self.body_chars + self.head_chars) // 4

    def to_ledger(self, n_steps: int = 0) -> CostLedger:
        return CostLedger(
            tokens_prompt=self.estimated_prompt_tokens,
            steps=n_steps,
            tool_calls=self.total_tool_results,
        )


@dataclass
class GcYield:
    archived_event_count: int = 0
    archived_input_tokens: int = 0
    archived_output_tokens: int = 0
    archived_payload_chars: int = 0


# ---------------------------------------------------------------------------
# Scorers
# ---------------------------------------------------------------------------

def compute_replay_metrics(
    messages: list,
    *,
    head_messages: Iterable | None = None,
) -> ReplayMetrics:
    """Walk a ``_events_to_messages`` output and collect body/head/dedup stats.

    ``messages`` is the ChatMessage list from the replay function.  We treat
    ``role in ("user", "tool")`` as *body* growth; whatever the caller passes in
    ``head_messages`` counts toward ``head_chars`` and is excluded from body.
    """
    body_chars = 0
    replayed_messages = 0
    tool_result_chars = 0
    tool_result_count = 0
    seen_tool_keys: set[str] = set()
    deduped = 0

    dedup_markers = _find_dedup_markers(messages)
    deduped = len(dedup_markers)

    for m in messages:
        content = (m.content or "") if not isinstance(m.content, list) else str(m.content)
        n = len(content)
        if m.role in ("user", "tool"):
            body_chars += n
        replayed_messages += 1
        if m.role == "tool":
            tool_result_chars += n
            tool_result_count += 1
            key = _tool_message_key(m)
            if key is not None:
                seen_tool_keys.add(key)

    head_chars = 0
    if head_messages is not None:
        for hm in head_messages:
            c = (hm.content or "") if not isinstance(hm.content, list) else str(hm.content)
            head_chars += len(c)

    return ReplayMetrics(
        body_chars=body_chars,
        head_chars=head_chars,
        replayed_messages=replayed_messages,
        replayed_tool_result_chars=tool_result_chars,
        unique_tool_keys=len(seen_tool_keys),
        deduped_tool_results=deduped,
        total_tool_results=tool_result_count,
    )


def short_memory_score(
    before: ReplayMetrics,
    after: ReplayMetrics,
    weights: tuple[float, float, float] = (0.4, 0.3, 0.3),
) -> dict[str, float]:
    """Composite score weighing cache-hit proxy, dedup ratio, body compression.

    The score compares *before* (baseline) to *after* (change).  Each dimension
    is normalised into a 0..1 contribution:

    cache_hit_proxy  = 1 - (after.body_chars / before.body_chars)
                      (positive if body shrank)
    dedup_ratio      = after.dedup_ratio   (already 0..1)
    body_compression = 1 - (after.body_chars / before.body_chars)
    """
    w_cache, w_dedup, w_compress = weights
    if before.body_chars == 0:
        cache_proxy = 1.0
        compress = 1.0
    else:
        ratio = after.body_chars / before.body_chars
        cache_proxy = max(0.0, min(1.0, 1.0 - ratio))
        compress = max(0.0, min(1.0, 1.0 - ratio))

    score = w_cache * cache_proxy + w_dedup * after.dedup_ratio + w_compress * compress
    return {
        "score": round(score, 4),
        "cache_hit_proxy": round(cache_proxy, 4),
        "dedup_ratio": round(after.dedup_ratio, 4),
        "body_compression": round(compress, 4),
        "before_body_chars": before.body_chars,
        "after_body_chars": after.body_chars,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DEDUPO_MARKER_PREFIX = "<dedup:"

def _find_dedup_markers(messages: list) -> list:
    """Count chat messages whose content is a dedup reference (placeholder)."""
    out = []
    for m in messages:
        content = (m.content or "") if not isinstance(m.content, list) else str(m.content)
        if content.startswith(_DEDUPO_MARKER_PREFIX):
            out.append(content)
    return out


def _tool_message_key(m) -> str | None:
    """Best-effort key for a tool message.

    We only have the ``content`` + ``tool_call_id``; the name is not on the
    tool message itself.  Return None so callers that need exact dedup keys
    should instead rely on ``compute_replay_metrics`` on the full turn.
    """
    return getattr(m, "tool_call_id", None) or _hash_content(m.content)


def _hash_content(content) -> str:
    c = (content or "") if not isinstance(content, list) else str(content)
    return hashlib.sha256(c.encode("utf-8", errors="replace")).hexdigest()[:16]
