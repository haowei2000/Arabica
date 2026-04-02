from __future__ import annotations

from datetime import UTC, datetime


def to_naive_utc(dt: datetime | None) -> datetime | None:
    """Convert a datetime to naive UTC.

    - If dt is None -> return None
    - If dt is timezone-aware -> convert to UTC and drop tzinfo
    - If dt is already naive -> return as-is
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


from datetime import timezone  # noqa: E402


def utc_now() -> datetime:
    """Return timezone-aware UTC datetime"""
    return datetime.now(UTC)
