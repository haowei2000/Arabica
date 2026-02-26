"""Celery tasks for document processing.

Single-task pipeline:
  process_document: download → structure → store (embed + persist to Context table)

Document status progression:
  pending → downloading → structuring → storing → completed  (or failed)
"""

import asyncio
import logging
from uuid import UUID

from celery.signals import worker_process_init, worker_process_shutdown

from aiwen.celery_worker.celery_app import celery_app

logger = logging.getLogger(__name__)

# Worker-level event loop - one per worker process
_worker_loop: asyncio.AbstractEventLoop | None = None
_db_initialized: bool = False


@worker_process_init.connect
def _init_worker_process(**_):
    """Initialize event loop and database connections when worker process starts."""
    global _worker_loop, _db_initialized

    _worker_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_worker_loop)

    from aiwen.extensions.database import _ensure_registered
    _ensure_registered()
    _db_initialized = True

    logger.info("Worker process initialized: event loop and database connections ready")


@worker_process_shutdown.connect
def _shutdown_worker_process(**_):
    """Clean up database connections and event loop when a worker process shuts down."""
    global _worker_loop, _db_initialized

    if _worker_loop is not None:
        async def _cleanup():
            from aiwen.extensions.database import dispose_all
            await dispose_all()

        try:
            _worker_loop.run_until_complete(_cleanup())
        except Exception as exc:
            logger.warning(f"Error during worker shutdown cleanup: {exc}")

        _worker_loop.close()
        _worker_loop = None

    _db_initialized = False
    logger.info("Worker process shutdown: database connections disposed")


def run_async(coro):
    """Run async coroutine using the worker's persistent event loop."""
    global _worker_loop, _db_initialized

    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)

    if not _db_initialized:
        from aiwen.extensions.database import _ensure_registered
        _ensure_registered()
        _db_initialized = True

    return _worker_loop.run_until_complete(coro)


@celery_app.task(
    bind=True,
    name="knowledge.process_document",
    max_retries=3,
    default_retry_delay=60,
    queue="knowledge",
)
def process_document(
    self,
    document_id: str,
    user_id: str,
    knowledge_id: str,
    object_key: str,
    mime_type: str,
    structure_type: str = "document",
    embedding_provider: str = "tongyi",
    embedding_model: str = "text-embedding-v3",
    embedding_dimension: int = 1024,
) -> dict:
    """Process a document in three phases: download → structure → store."""

    async def _execute() -> dict:
        from sqlalchemy import update

        from aiwen.core.enums import ContextType
        from aiwen.extensions.database import get_session
        from aiwen.extensions.storage.global_storage import get_global_s3_storage
        from aiwen.models.context.context import Context
        from aiwen.models.context.knowledge.documents import Document
        from aiwen.plugins.structurers import dispatch_structure
        from aiwen.services.context.knowledge.embeddings import EmbeddingService
        from aiwen.services.context.knowledge.parser import DocumentParser

        doc_uuid = UUID(document_id)

        # ── Phase 1: Download ────────────────────────────────────────────────
        async with get_session("aiwen") as session:
            await session.execute(
                update(Document).where(Document.id == doc_uuid).values(status="downloading")
            )
            await session.commit()

        file_data = get_global_s3_storage().get_bytes(object_key)

        # ── Phase 2: Structure ───────────────────────────────────────────────
        async with get_session("aiwen") as session:
            await session.execute(
                update(Document).where(Document.id == doc_uuid).values(status="structuring")
            )
            await session.commit()

        text_content = DocumentParser().parse(file_data, mime_type)

        if not text_content.strip():
            logger.warning(f"process_document: document {document_id} has no text")
            async with get_session("aiwen") as session:
                await session.execute(
                    update(Document)
                    .where(Document.id == doc_uuid)
                    .values(status="completed", chunk_count=0)
                )
                await session.commit()
            return {"document_id": document_id, "knowledge_id": knowledge_id,
                    "status": "completed", "section_count": 0}

        sections = dispatch_structure(text_content, mime_type, structure_type)

        # ── Phase 3: Store (embed + persist) ─────────────────────────────────
        async with get_session("aiwen") as session:
            await session.execute(
                update(Document).where(Document.id == doc_uuid).values(status="storing")
            )
            await session.commit()

        embedding_service = EmbeddingService(
            provider=embedding_provider,
            model=embedding_model,
            dimension=embedding_dimension,
        )
        embedding_field = embedding_service.get_embedding_field_name()

        valid_sections = [sec for sec in sections if sec["content"].strip()]
        texts = [sec["content"] for sec in valid_sections]
        embeddings = embedding_service.embed_texts_batch(texts, batch_size=10) if texts else []

        _RESERVED = {"title", "level", "content", "position"}

        async with get_session("aiwen") as session:
            doc_result = await session.get(Document, doc_uuid)
            doc_name = doc_result.original_name if doc_result else document_id

            context_records = []
            for i, sec in enumerate(valid_sections):
                extra = {k: v for k, v in sec.items() if k not in _RESERVED}
                record = Context(
                    user_id=UUID(user_id),
                    source_id=UUID(knowledge_id),
                    context_type=ContextType.CHUNK.value,
                    glance=sec["title"] or sec["content"][:80],
                    content=sec["content"],
                    tags=["knowledge", "document", structure_type],
                    meta={
                        "document_id": document_id,
                        "document_name": doc_name,
                        "knowledge_id": knowledge_id,
                        "section_title": sec["title"],
                        "section_level": sec["level"],
                        "position": sec["position"],
                        "embedding_model": embedding_model,
                        "embedding_provider": embedding_provider,
                        **extra,
                    },
                )
                if i < len(embeddings) and embeddings[i] is not None:
                    setattr(record, embedding_field, embeddings[i])
                context_records.append(record)

            session.add_all(context_records)
            await session.execute(
                update(Document)
                .where(Document.id == doc_uuid)
                .values(
                    content=text_content[:1000],
                    status="completed",
                    chunk_count=len(context_records),
                )
            )
            await session.commit()

        logger.info(
            f"process_document: {document_id} → {len(context_records)} sections stored"
        )
        return {
            "document_id": document_id,
            "knowledge_id": knowledge_id,
            "status": "completed",
            "section_count": len(context_records),
            "embedding_model": embedding_model,
            "embedding_dimension": embedding_dimension,
        }

    try:
        return run_async(_execute())
    except Exception as exc:
        logger.error(f"process_document failed for {document_id}: {exc}")
        _exc = exc  # capture before except scope clears it

        async def _mark_failed():
            from sqlalchemy import update
            from aiwen.extensions.database import get_session
            from aiwen.models.context.knowledge.documents import Document

            if self.request.retries >= self.max_retries - 1:
                async with get_session("aiwen") as session:
                    await session.execute(
                        update(Document)
                        .where(Document.id == UUID(document_id))
                        .values(status="failed", error_message=str(_exc))
                    )
                    await session.commit()

        run_async(_mark_failed())
        self.retry(exc=_exc)


def process_document_structured(
    document_id: str,
    knowledge_id: str,
    object_key: str,
    mime_type: str,
    user_id: str,
    embedding_provider: str = "tongyi",
    embedding_model: str = "text-embedding-v3",
    embedding_dimension: int = 1024,
    structure_type: str = "document",
) -> str:
    """Submit the document processing task (download → structure → store).

    Returns the Celery task ID.
    """
    result = process_document.apply_async(
        kwargs={
            "document_id": document_id,
            "user_id": user_id,
            "knowledge_id": knowledge_id,
            "object_key": object_key,
            "mime_type": mime_type,
            "structure_type": structure_type,
            "embedding_provider": embedding_provider,
            "embedding_model": embedding_model,
            "embedding_dimension": embedding_dimension,
        },
        queue="knowledge",
    )
    logger.info(
        f"process_document_structured: submitted task {result.id} for document {document_id}"
    )
    return result.id
