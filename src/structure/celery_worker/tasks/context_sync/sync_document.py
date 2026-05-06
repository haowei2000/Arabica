"""Celery task: process and sync an uploaded document to the Context table.

Pipeline per file type
----------------------
- .md / text/markdown  → MarkdownStructurer → one Context record per section
- Everything else      → MarkItDown → MarkdownStructurer → one Context record
  per section

This mirrors the SkillStructurer pattern: each structural unit becomes its own
path-addressable Context row so retrieval and navigation work at section level.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from structure.celery_worker.celery_app import celery_app
from structure.celery_worker.tasks.context_sync._base import (
    _fetch_embedding_service,
    _store_embedding,
    _upsert_context_at_path,
)
from structure.celery_worker.tasks.knowledge_tasks import run_async
from structure.core.enums import ContextType
from structure.services.context.context_embedding import embed_batch_for_context
from structure.utils.context import build_path

logger = logging.getLogger(__name__)


def _is_markdown(mime_type: str, original_name: str) -> bool:
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    return ext in {"md", "markdown"} or "markdown" in (mime_type or "").lower()


def _sanitize_text(text: str) -> str:
    """Remove characters that are invalid for PostgreSQL UTF-8 text fields."""
    text = text.replace("\x00", "")
    return "".join(
        char
        for char in text
        if ord(char) in {9, 10, 13} or ord(char) >= 32
    )


def _decode_text_bytes(file_data: bytes) -> str:
    """Decode uploaded text/Markdown bytes with common fallback encodings."""
    for encoding in ("utf-8", "gbk", "gb2312", "latin-1"):
        try:
            return _sanitize_text(file_data.decode(encoding))
        except UnicodeDecodeError:
            continue
    return _sanitize_text(file_data.decode("utf-8", errors="replace"))


def _parse_markdown_upload(
    file_data: bytes,
    mime_type: str,  # noqa: ARG001
    original_name: str,  # noqa: ARG001
) -> str:
    """Decode uploaded Markdown bytes without converting Markdown syntax."""
    return _decode_text_bytes(file_data)


def _convert_with_markitdown(
    file_data: bytes,
    mime_type: str,  # noqa: ARG001
    original_name: str,
) -> str:
    """Convert arbitrary uploaded bytes to Markdown using MarkItDown."""
    from markitdown import MarkItDown

    stream = io.BytesIO(file_data)
    stream.name = original_name

    extension = Path(original_name).suffix.lower() or None
    result = MarkItDown(enable_plugins=False).convert_stream(
        stream,
        file_extension=extension,
    )
    text = getattr(result, "text_content", "") or ""
    return _sanitize_text(text)


def _document_to_markdown(
    file_data: bytes,
    mime_type: str,
    original_name: str,
) -> tuple[str, str]:
    """Return ``(markdown_text, conversion_source)`` for an uploaded document."""
    if _is_markdown(mime_type, original_name):
        return _parse_markdown_upload(file_data, mime_type, original_name), "markdown"

    return _convert_with_markitdown(file_data, mime_type, original_name), "markitdown"


def _section_to_core(
    section: dict[str, Any],
    *,
    doc_root: tuple[str, ...],
    original_name: str,
    conversion_source: str,
) -> dict[str, Any] | None:
    """Map one Markdown section to a Context core payload."""
    content = (section.get("content") or "").strip()
    title = (section.get("title") or "").strip()
    if not content and not title:
        return None
    if not content:
        content = title

    section_path = section.get("section_path") or title or str(section["position"])
    return {
        "path": build_path(*doc_root, section_path),
        "glance": title or original_name,
        "content": content,
        "meta": {
            "conversion_source": conversion_source,
            "section_title": title,
            "section_level": section.get("level"),
            "section_path": section.get("section_path"),
            "section_position": section.get("position"),
            "structure_type": section.get("structure_type"),
        },
    }


def _structure_document_markdown(
    markdown_text: str,
    *,
    mime_type: str,
    original_name: str,
    knowledge_name: str,
    conversion_source: str,
) -> list[dict[str, Any]]:
    """Structure Markdown text into path-addressable Context core payloads."""
    from structure.plugins.structurers.markdown_structure import MarkdownStructurer

    doc_root = ("knowledge", knowledge_name, "documents", original_name)
    sections = MarkdownStructurer().structure(markdown_text, mime_type)
    cores = [
        core
        for section in sections
        if (
            core := _section_to_core(
                section,
                doc_root=doc_root,
                original_name=original_name,
                conversion_source=conversion_source,
            )
        )
    ]

    if cores:
        return cores

    return [
        {
            "path": build_path(*doc_root),
            "glance": original_name,
            "content": markdown_text,
            "meta": {
                "conversion_source": conversion_source,
                "section_title": "",
                "section_level": 1,
                "section_path": "/",
                "section_position": 0,
                "structure_type": "markdown",
            },
        }
    ]


@celery_app.task(
    bind=True,
    name="context_sync.sync_document",
    max_retries=3,
    default_retry_delay=60,
    queue="knowledge",
)
def sync_document_to_contexts(
    self,
    document_id: str,
    user_id: str,
    knowledge_id: str,
    knowledge_name: str,
    object_key: str,
    mime_type: str,
    original_name: str,
) -> dict:
    """Download → structure → store → embed an uploaded document.

    Markdown files are split directly. Other file types are converted to
    Markdown with MarkItDown first, then split by the same Markdown structurer.
    """

    async def _execute() -> dict:
        from sqlalchemy import update

        from structure.extensions.database import get_session
        from structure.extensions.storage.global_storage import get_global_s3_storage
        from structure.models.context.knowledge.documents import Document

        doc_uuid = UUID(document_id)

        # ── Phase 1: Download ────────────────────────────────────────────────
        async with get_session("structure") as session:
            await session.execute(
                update(Document)
                .where(Document.id == doc_uuid)
                .values(status="downloading")
            )
            await session.commit()

        file_data = get_global_s3_storage().get_bytes(object_key)

        # ── Phase 2: Convert to Markdown ─────────────────────────────────────
        async with get_session("structure") as session:
            await session.execute(
                update(Document)
                .where(Document.id == doc_uuid)
                .values(status="structuring")
            )
            await session.commit()

        markdown_content, conversion_source = _document_to_markdown(
            file_data,
            mime_type,
            original_name,
        )

        if not markdown_content.strip():
            logger.warning(f"sync_document: document {document_id} yielded no text")
            async with get_session("structure") as session:
                await session.execute(
                    update(Document)
                    .where(Document.id == doc_uuid)
                    .values(status="completed", chunk_count=0)
                )
                await session.commit()
            return {
                "document_id": document_id,
                "status": "completed",
                "section_count": 0,
            }

        # ── Phase 3: Structure ───────────────────────────────────────────────
        cores = _structure_document_markdown(
            markdown_content,
            mime_type="text/markdown",
            original_name=original_name,
            knowledge_name=knowledge_name,
            conversion_source=conversion_source,
        )

        # ── Phase 4: Upsert Context records ──────────────────────────────────
        async with get_session("structure") as session:
            await session.execute(
                update(Document).where(Document.id == doc_uuid).values(status="storing")
            )
            await session.commit()

        ctx_ids_need_embed: list[tuple[str, str]] = []
        common_meta = {
            "document_id": document_id,
            "knowledge_id": knowledge_id,
            "document_name": original_name,
            "mime_type": mime_type,
        }

        async with get_session("structure") as session:
            for core in cores:
                meta = {**common_meta, **core["meta"]}
                ctx, needs_embedding = await _upsert_context_at_path(
                    session,
                    user_id=user_id,
                    context_type=ContextType.CHUNK,
                    source_id=document_id,
                    path=core["path"],
                    glance=core["glance"],
                    content=core["content"],
                    tags=["knowledge", "document"],
                    meta=meta,
                )
                await session.flush()
                if needs_embedding:
                    embed_text = " ".join(
                        filter(None, [core["glance"], core["content"]])
                    )
                    ctx_ids_need_embed.append((str(ctx.id), embed_text))

            emb_svc = await _fetch_embedding_service(session)

            await session.execute(
                update(Document)
                .where(Document.id == doc_uuid)
                .values(
                    content=markdown_content[:1000],
                    status="completed",
                    chunk_count=len(cores),
                )
            )
            await session.commit()

        logger.info(
            f"sync_document: {document_id} ({original_name}) → {len(cores)} context(s)"
        )

        # ── Phase 5: Embed (batched — one session for all chunks) ────────────────
        to_embed = [(cid, txt) for cid, txt in ctx_ids_need_embed if txt.strip()]
        if to_embed:
            texts = [txt for _, txt in to_embed]
            vectors, field = embed_batch_for_context(texts, emb_svc)
            async with get_session("structure") as session:
                for (ctx_id, _), vector in zip(to_embed, vectors):  # noqa: B905
                    await _store_embedding(session, ctx_id, vector, field)
                await session.commit()
            logger.info(
                f"sync_document: embedded {len(to_embed)} chunk(s) for {document_id}"
            )

        return {
            "document_id": document_id,
            "knowledge_id": knowledge_id,
            "status": "completed",
            "section_count": len(cores),
        }

    try:
        return run_async(_execute())
    except Exception as exc:
        logger.error(f"sync_document failed for {document_id}: {exc}")
        _exc = exc

        async def _mark_failed():
            from sqlalchemy import update

            from structure.extensions.database import get_session
            from structure.models.context.knowledge.documents import Document

            if self.request.retries >= self.max_retries - 1:
                async with get_session("structure") as session:
                    await session.execute(
                        update(Document)
                        .where(Document.id == UUID(document_id))
                        .values(status="failed", error_message=str(_exc))
                    )
                    await session.commit()

        run_async(_mark_failed())
        self.retry(exc=_exc)


def submit_sync_document(
    document_id: str,
    knowledge_id: str,
    knowledge_name: str,
    object_key: str,
    mime_type: str,
    user_id: str,
    original_name: str,
) -> str:
    """Submit a document sync task. Returns the Celery task ID."""
    result = sync_document_to_contexts.apply_async(
        kwargs={
            "document_id": document_id,
            "user_id": user_id,
            "knowledge_id": knowledge_id,
            "knowledge_name": knowledge_name,
            "object_key": object_key,
            "mime_type": mime_type,
            "original_name": original_name,
        },
        queue="knowledge",
    )
    logger.info(f"submit_sync_document: task {result.id} for document {document_id}")
    return result.id
