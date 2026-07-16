"""Rating read-path: threshold filtering and ranking-stage blending.

The write path (``rate_context`` tool -> ``CONTEXT_RATED`` event ->
denormalised ``rating_sum``/``rating_count`` aggregate) records how useful
consulted entries were.  This module is the read path: retrieval callers
use it to discard entries prior runs judged harmful (filter stage) and to
blend the rating mean into similarity ranking (ranking stage) as

    s = alpha * similarity + (1 - alpha) * rating_avg

Entries without ratings are unaffected: they pass the threshold and rank
by similarity alone, so enabling the read path never changes behaviour
until ratings exist.
"""

from __future__ import annotations

DEFAULT_RATING_ALPHA = 0.7


def rating_average(rating_sum: float | None, rating_count: int | None) -> float | None:
    """Return the rating mean, or ``None`` when the entry has no ratings."""
    count = int(rating_count or 0)
    if count <= 0:
        return None
    return float(rating_sum or 0.0) / count


def passes_rating_threshold(rating_avg: float | None, min_rating: float | None) -> bool:
    """Filter stage: drop entries whose rating mean falls below the threshold.

    Unrated entries (``rating_avg is None``) always pass, as does everything
    when no threshold is configured.
    """
    if min_rating is None or rating_avg is None:
        return True
    return rating_avg >= min_rating


def blend_score(
    similarity: float,
    rating_avg: float | None,
    *,
    alpha: float = DEFAULT_RATING_ALPHA,
) -> float:
    """Ranking stage: convex combination of similarity and rating mean.

    ``alpha`` weights similarity; ``1 - alpha`` weights the rating mean in
    ``[-1, 1]``.  Unrated entries rank by similarity alone.
    """
    if rating_avg is None:
        return similarity
    alpha = min(max(alpha, 0.0), 1.0)
    return alpha * similarity + (1.0 - alpha) * rating_avg
