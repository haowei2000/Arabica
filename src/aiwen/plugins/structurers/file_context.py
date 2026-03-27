"""Shared data types for the file pipeline.

Kept in a separate module so both ``stages.py`` and ``file_structure.py``
can import them without creating a circular dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aiwen.schemas.context.context_schema import ContextCore


@dataclass
class FileInput:
    """Caller-supplied descriptor for a single S3-backed file.

    Attributes:
        s3_key:       Object key in the S3/MinIO bucket.
        file_name:    Display name used in glance strings and path segments
                      (e.g. ``"guide.md"``).
        base_path:    Parent context path that prefixes every emitted chunk
                      (e.g. ``"/skill/my-skill"``).
        content_type: MIME type; drives text-structurer selection in SplitStage.
        size:         File size in bytes (informational only).
        extra:        Any additional metadata forwarded from the files dict.
    """

    s3_key: str
    file_name: str
    base_path: str
    content_type: str = ""
    size: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class FileContext:
    """Mutable data envelope that flows through a file pipeline.

    Each stage reads and writes fields on this object, then returns it.
    Stages should never replace the instance — mutate in place so all stages
    share the same reference.

    Attributes:
        file_input: Original descriptor, treated as read-only by convention.
        raw_bytes:  Populated by a fetch stage (e.g. S3FetchStage, BytesStage).
        text:       Populated by a decode stage.
        sections:   Populated by a split stage (list of section dicts).
        chunks:     Populated by a build stage (final ContextCore output).
        errors:     Accumulated warning strings; does NOT abort the pipeline.
    """

    file_input: FileInput
    raw_bytes: bytes | None = None
    text: str | None = None
    sections: list[Any] = field(default_factory=list)
    chunks: list[ContextCore] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
