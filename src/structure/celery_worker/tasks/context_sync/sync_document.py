"""Celery task: process and sync an uploaded document to the Context table.

Pipeline:
- Convert every uploaded file to Markdown via MarkItDown.
- Split the Markdown into path-addressable sections.
- Store each section as a Context chunk and embed it.
"""

import logging
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


def _dedupe_path(path: str, seen_paths: set[str], position: int) -> str:
    """Avoid overwriting repeated section paths in one document."""
    if path not in seen_paths:
        return path
    return f"{path}/section_{position + 1}"


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

    Every supported file type is first converted to Markdown, then split into
    per-section Context records.
    """

    async def _execute() -> dict:
        from sqlalchemy import select, update

        from structure.extensions.database import get_session
        from structure.extensions.storage.global_storage import get_global_s3_storage
        from structure.models.context.knowledge.documents import Document
        from structure.models.context.knowledge.knowledge import Knowledge
        from structure.services.context.knowledge.parser import DocumentParser

        doc_uuid = UUID(document_id)
        knowledge_uuid = UUID(knowledge_id)

        # ── Phase 1: Download ────────────────────────────────────────────────
        async with get_session("structure") as session:
            await session.execute(
                update(Document)
                .where(Document.id == doc_uuid)
                .values(status="downloading")
            )
            await session.commit()

        file_data = get_global_s3_storage().get_bytes(object_key)

        # ── Phase 2: Convert to Markdown ────────────────────────────────────
        async with get_session("structure") as session:
            await session.execute(
                update(Document)
                .where(Document.id == doc_uuid)
                .values(status="structuring")
            )
            await session.commit()

        markdown_content = DocumentParser().parse(file_data, mime_type, original_name)

        if not markdown_content.strip():
            logger.warning(f"sync_document: document {document_id} yielded no text")
            async with get_session("structure") as session:
                previous_chunk_count = await session.scalar(
                    select(Document.chunk_count).where(Document.id == doc_uuid)
                )
                await session.execute(
                    update(Document)
                    .where(Document.id == doc_uuid)
                    .values(status="completed", chunk_count=0, content="")
                )
                if previous_chunk_count:
                    await session.execute(
                        update(Knowledge)
                        .where(Knowledge.id == knowledge_uuid)
                        .values(
                            chunk_count=Knowledge.chunk_count - previous_chunk_count
                        )
                    )
                await session.commit()
            return {
                "document_id": document_id,
                "status": "completed",
                "section_count": 0,
            }

        # ── Phase 3: Structure ───────────────────────────────────────────────
        # Path root: /knowledge/<name>/documents/<filename>
        doc_root = ("knowledge", knowledge_name, "documents", original_name)

        from structure.plugins.structurers.markdown_structure import MarkdownStructurer

        sections = MarkdownStructurer().structure(markdown_content, "text/markdown")
        cores = []
        seen_paths: set[str] = set()
        for sec in sections:
            content = (sec.get("content") or "").strip()
            if not content:
                continue

            title = (sec.get("title") or "").strip()
            position = int(sec.get("position") or 0)
            section_path = sec.get("section_path") or ""
            path = build_path(
                *doc_root,
                section_path
                if section_path != "/"
                else title or f"section-{position + 1}",
            )
            path = _dedupe_path(path, seen_paths, position)
            seen_paths.add(path)
            cores.append(
                {
                    "path": path,
                    "glance": title or original_name,
                    "content": content,
                }
            )

        if not cores:
            cores = [
                {
                    "path": build_path(*doc_root),
                    "glance": original_name,
                    "content": markdown_content,
                }
            ]

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
            "converted_to": "markdown",
            "source_mime_type": mime_type,
        }

        async with get_session("structure") as session:
            previous_chunk_count = (
                await session.scalar(
                    select(Document.chunk_count).where(Document.id == doc_uuid)
                )
                or 0
            )
            emb_svc = await _fetch_embedding_service(session)
            embedding_field = emb_svc.get_embedding_field_name()
            for core in cores:
                ctx, needs_embedding = await _upsert_context_at_path(
                    session,
                    user_id=user_id,
                    context_type=ContextType.CHUNK,
                    source_id=document_id,
                    path=core["path"],
                    glance=core["glance"],
                    content=core["content"],
                    tags=["knowledge", "document"],
                    meta=common_meta,
                )
                await session.flush()
                missing_embedding = getattr(ctx, embedding_field, None) is None
                if needs_embedding or missing_embedding:
                    embed_text = " ".join(
                        filter(None, [core["glance"], core["content"]])
                    )
                    ctx_ids_need_embed.append((str(ctx.id), embed_text))

            await session.execute(
                update(Document)
                .where(Document.id == doc_uuid)
                .values(
                    content=markdown_content[:1000],
                    status="completed",
                    chunk_count=len(cores),
                )
            )
            chunk_delta = len(cores) - previous_chunk_count
            if chunk_delta:
                await session.execute(
                    update(Knowledge)
                    .where(Knowledge.id == knowledge_uuid)
                    .values(chunk_count=Knowledge.chunk_count + chunk_delta)
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
