"""Built-in Stage implementations for the file pipeline.

All stages operate on :class:`FileContext` and can be freely combined into a
:class:`~structure.core.interfaces.pipeline.Pipeline`.

Available stages
----------------
S3TextStage         Download from S3 and decode to text (UTF-8, latin-1 fallback).
BytesStage          Inject pre-loaded bytes and decode to text (testing / in-memory).
SplitStage          Split text into section dicts via MIME dispatch.
BuildChunksStage    Convert sections into ContextCore chunks.

MIME dispatch table (SplitStage)
---------------------------------
text/csv, application/csv, text/html          → TableStructurer
text/x-python, text/javascript, text/typescript,
text/x-java, text/x-go, application/javascript → CodeStructurer
everything else                                → DocumentStructurer
"""

from __future__ import annotations

import logging

from structure.core.interfaces.pipeline import Stage
from structure.core.interfaces.structurer import BaseStructurer
from structure.plugins.structurers.file_context import FileContext
from structure.schemas.context.context_schema import ContextCore
from structure.utils.context import slugify

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MIME → text-structurer kind
# ---------------------------------------------------------------------------
_MIME_KIND: dict[str, str] = {
    "text/csv": "table",
    "application/csv": "table",
    "text/html": "table",
    "text/x-python": "code",
    "application/x-python": "code",
    "text/javascript": "code",
    "application/javascript": "code",
    "text/typescript": "code",
    "text/x-typescript": "code",
    "text/x-java": "code",
    "text/x-go": "code",
}


def pick_text_structurer(content_type: str) -> BaseStructurer:
    """Return the most appropriate text structurer for *content_type*.

    Importable so callers can re-use the same MIME dispatch logic outside
    of a full pipeline (e.g. in tests or one-off conversions).
    """
    from structure.plugins.structurers.code_structure import CodeStructurer
    from structure.plugins.structurers.document import DocumentStructurer
    from structure.plugins.structurers.table import TableStructurer

    mime = (content_type or "").lower().split(";")[0].strip()
    kind = _MIME_KIND.get(mime)
    if kind == "table":
        return TableStructurer()
    if kind == "code":
        return CodeStructurer()
    return DocumentStructurer()


# ---------------------------------------------------------------------------
# Fetch + decode stage  — populate ctx.text directly from S3
# ---------------------------------------------------------------------------
class S3TextStage(Stage[FileContext]):
    """Download raw bytes from S3 and decode them to text in one step.

    Tries UTF-8 first; falls back to latin-1.  On download failure the error
    is recorded in ``ctx.errors`` and ``ctx.text`` remains ``None`` so that
    ``BuildChunksStage`` emits a stub node instead of crashing.
    """

    def process(self, ctx: FileContext) -> FileContext:
        try:
            from structure.extensions.storage.global_storage import (
                get_global_s3_storage,
            )

            raw = get_global_s3_storage().get_bytes(ctx.file_input.s3_key)
        except Exception as exc:
            msg = f"S3TextStage: cannot download {ctx.file_input.s3_key} — {exc}"
            logger.warning(msg)
            ctx.errors.append(msg)
            return ctx

        try:
            ctx.text = raw.decode("utf-8")
        except UnicodeDecodeError:
            ctx.text = raw.decode("latin-1")
        return ctx


class BytesStage(Stage[FileContext]):
    """Inject pre-loaded bytes and decode them to text in one step.

    Useful for testing (bypass S3) or when bytes are already available
    in memory (e.g. from a multipart upload handler).

    Args:
        raw_bytes: The bytes to decode into ``ctx.text``.
    """

    def __init__(self, raw_bytes: bytes) -> None:
        self._raw = raw_bytes

    def process(self, ctx: FileContext) -> FileContext:
        try:
            ctx.text = self._raw.decode("utf-8")
        except UnicodeDecodeError:
            ctx.text = self._raw.decode("latin-1")
        return ctx


# ---------------------------------------------------------------------------
# Split stage  — populate ctx.sections
# ---------------------------------------------------------------------------
class SplitStage(Stage[FileContext]):
    """Split ``ctx.text`` into a list of section dicts.

    Uses :func:`pick_text_structurer` to auto-select the right structurer
    from the file's MIME type.  Pass an explicit *structurer* to override:

        SplitStage(structurer=CodeStructurer())

    Skips silently when ``ctx.text`` is ``None``.
    """

    def __init__(self, structurer: BaseStructurer | None = None) -> None:
        self._structurer = structurer

    def process(self, ctx: FileContext) -> FileContext:
        if ctx.text is None:
            return ctx
        mime = ctx.file_input.content_type
        s = self._structurer or pick_text_structurer(mime)
        ctx.sections = s.structure(ctx.text, mime)  # type: ignore[call-arg]
        return ctx


# ---------------------------------------------------------------------------
# Build stage  — populate ctx.chunks
# ---------------------------------------------------------------------------
class BuildChunksStage(Stage[FileContext]):
    """Convert ``ctx.sections`` into a :class:`ContextCore` list.

    Emits two levels of context nodes per file:

    * **Level 2** ``<base>/<file>``             — indented section-outline node
    * **Level 3** ``<base>/<file>/<section>``   — individual section content

    When ``ctx.sections`` is empty (fetch or decode failed upstream) a single
    stub node is emitted so the context path always exists.
    """

    def process(self, ctx: FileContext) -> FileContext:
        fi = ctx.file_input
        file_slug = slugify(f"{fi.base_path}/{fi.file_name}")

        if not ctx.sections:
            ctx.chunks.append(
                ContextCore(
                    glance=fi.file_name,
                    content=f"{fi.file_name}:\n  (content unavailable)",
                    path=file_slug,
                )
            )
            return ctx

        # Level 2: section outline
        outline_lines = [f"{fi.file_name}:"]
        for sec in ctx.sections:
            indent = "  " * max(0, sec["level"] - 1)
            title = sec.get("title") or "(untitled)"
            outline_lines.append(f"{indent}- {title}")

        ctx.chunks.append(
            ContextCore(
                glance=fi.file_name,
                content="\n".join(outline_lines),
                path=file_slug,
            )
        )

        # Level 3: per-section content
        for sec in ctx.sections:
            sec_title = sec.get("title") or f"section-{sec['position']}"
            ctx.chunks.append(
                ContextCore(
                    glance=f"{fi.file_name} / {sec_title}",
                    content=sec["content"],
                    path=slugify(f"{fi.base_path}/{fi.file_name}/{sec_title}"),
                )
            )

        return ctx
