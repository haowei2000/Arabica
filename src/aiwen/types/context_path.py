"""ContextPath — a validated `/`-separated virtual path type."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, PlainSerializer

# Each segment: non-empty, no slashes, no control chars.
_SEGMENT_RE = re.compile(r"^[^\x00-\x1f/]+$")


def _validate_context_path(value: str) -> str:
    """Validate that *value* is a `/`-separated virtual path.

    Rules:
    - Must start with `/`
    - Must not end with `/` (except the root `/` itself)
    - Each segment between slashes must be non-empty (no `//`)
    - Segments must not contain control characters
    - Maximum length 1024 characters
    """
    if not isinstance(value, str):
        raise TypeError(f"ContextPath must be str, got {type(value).__name__}")

    if len(value) > 1024:
        raise ValueError("ContextPath must be at most 1024 characters")

    if not value.startswith("/"):
        raise ValueError("ContextPath must start with '/'")

    # Root path is valid
    if value == "/":
        return value

    if value.endswith("/"):
        raise ValueError("ContextPath must not end with '/' (except root '/')")

    segments = value[1:].split("/")  # strip leading '/'
    for seg in segments:
        if not seg:
            raise ValueError("ContextPath must not contain empty segments (consecutive '//')")
        if not _SEGMENT_RE.match(seg):
            raise ValueError(
                f"Invalid path segment '{seg}': must not contain control characters or '/'"
            )

    return value


ContextPath = Annotated[
    str,
    AfterValidator(_validate_context_path),
    PlainSerializer(lambda v: v, return_type=str),
]
"""A `/`-separated virtual folder path (e.g. ``/projects/demo/docs``).

Used in both Pydantic schemas and as a Python type hint for SQLAlchemy models.
"""
