"""Unit tests for the rating read-path (filter + blend)."""

import pytest
from structure.services.context_service.rating import (
    DEFAULT_RATING_ALPHA,
    blend_score,
    passes_rating_threshold,
    rating_average,
)


@pytest.mark.unit
def test_rating_average_derives_mean_and_handles_unrated():
    assert rating_average(2.0, 4) == pytest.approx(0.5)
    assert rating_average(-3.0, 3) == pytest.approx(-1.0)
    assert rating_average(0.0, 0) is None
    assert rating_average(None, None) is None


@pytest.mark.unit
def test_threshold_drops_consistently_harmful_entries_only():
    assert passes_rating_threshold(-0.8, min_rating=-0.5) is False
    assert passes_rating_threshold(-0.5, min_rating=-0.5) is True
    assert passes_rating_threshold(0.9, min_rating=-0.5) is True
    # Unrated entries and unconfigured thresholds always pass.
    assert passes_rating_threshold(None, min_rating=-0.5) is True
    assert passes_rating_threshold(-1.0, min_rating=None) is True


@pytest.mark.unit
def test_blend_is_convex_combination_with_default_alpha():
    assert blend_score(1.0, None) == pytest.approx(1.0)
    assert blend_score(0.6, 1.0) == pytest.approx(
        DEFAULT_RATING_ALPHA * 0.6 + (1 - DEFAULT_RATING_ALPHA) * 1.0
    )
    assert blend_score(0.6, -1.0, alpha=0.5) == pytest.approx(-0.2)
    # alpha=1 ignores ratings; alpha=0 ranks purely by rating.
    assert blend_score(0.4, 0.9, alpha=1.0) == pytest.approx(0.4)
    assert blend_score(0.4, 0.9, alpha=0.0) == pytest.approx(0.9)


@pytest.mark.unit
def test_blend_reorders_equal_similarity_by_rating():
    helpful = blend_score(0.5, 0.8)
    misleading = blend_score(0.5, -0.8)
    unrated = blend_score(0.5, None)
    assert helpful > unrated > misleading
