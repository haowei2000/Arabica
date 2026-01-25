"""Celery tasks for document processing.

This module contains three chained tasks:
1. download_chunk_and_store: Download file from MinIO, parse, chunk, and store to Chunk table
2. embed_chunks: Generate embeddings for stored chunks
3. link_chunks_to_context: Link chunks to Context table for knowledge base
"""

import asyncio
import logging
from typing import Any
from uuid import UUID

from celery import chain

from aiwen.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def run_async(coro):
    """Run async coroutine in a new event loop safely for Celery workers.

    This function resets the global database state before running to avoid
    event loop mismatch errors when engines are cached across different loops.
    """
    from aiwen.extensions.database import _engines, _sessionmakers, _bases, _dependency_functions
    import aiwen.extensions.database as db_module

    # Reset global database state to avoid event loop mismatch
    # Each Celery task gets a new event loop, so cached engines from
    # previous tasks will have connections bound to the old loop
    _engines.clear()
    _sessionmakers.clear()
    _bases.clear()
    _dependency_functions.clear()
    db_module._initialized = False

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        # Clean up: dispose engines before closing loop
        async def _cleanup():
            for engine in list(_engines.values()):
                try:
                    await engine.dispose()
                except Exception:
                    pass

        try:
            loop.run_until_complete(_cleanup())
        except Exception:
            pass
        loop.close()


@celery_app.task(
    bind=True,
    name="knowledge.download_chunk_and_store",
    max_retries=3,
    default_retry_delay=60,
    queue='knowledge'
)
def download_chunk_and_store(
        self,
        document_id: str,
        user_id: str,
        bucket: str,
        object_key: str,
        mime_type: str,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
) -> dict[str, Any]:
    """Download file from MinIO, parse content, chunk, and store to Chunk table.

    Args:
        self: Celery task instance.
        document_id: Document UUID.
        user_id: User UUID who owns the document.
        bucket: MinIO bucket name.
        object_key: Object key/path in MinIO.
        mime_type: MIME type of the document.
        chunk_size: Maximum chunk size in characters.
        chunk_overlap: Overlap between chunks.

    Returns:
        dict: Contains document_id, user_id, and list of chunk IDs.
    """
    logger.info(
        f"Task download_chunk_and_store started: document_id={document_id}, "
        f"bucket={bucket}, object_key={object_key}"
    )

    async def _execute():
        from aiwen.extensions.storage.global_storage import get_global_s3_storage
        from aiwen.services.knowledge import DocumentParser, TextChunker
        from aiwen.services.knowledge.chunker import ChunkConfig
        from aiwen.extensions.database import get_session
        from aiwen.models.agents.docments import Document
        from aiwen.models.agents.chunk import Chunk
        from sqlalchemy import update

        chunk_ids = []

        async with get_session("aiwen") as session:
            # Update status to downloading
            await session.execute(
                update(Document).where(Document.id == UUID(document_id)).values(status="downloading")
            )
            await session.commit()

        # Download file from S3 (RustFS) - sync operation
        storage = get_global_s3_storage()
        file_data = storage.get_bytes(object_key)

        async with get_session("aiwen") as session:
            await session.execute(
                update(Document).where(Document.id == UUID(document_id)).values(status="parsing")
            )
            await session.commit()

        # Parse document content - sync operation
        parser = DocumentParser()
        text_content = parser.parse(file_data, mime_type)

        if not text_content.strip():
            logger.warning(f"Document {document_id} has no extractable text content")
            async with get_session("aiwen") as session:
                await session.execute(
                    update(Document).where(Document.id == UUID(document_id)).values(status="completed", chunk_count=0)
                )
                await session.commit()
            return {
                "document_id": document_id,
                "user_id": user_id,
                "chunk_ids": [],
                "total_chunks": 0,
                "status": "empty",
            }

        async with get_session("aiwen") as session:
            await session.execute(
                update(Document).where(Document.id == UUID(document_id)).values(status="chunking")
            )
            await session.commit()

        # Split into chunks - sync operation
        chunker_config = ChunkConfig(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        chunker = TextChunker(chunker_config)
        chunks = chunker.chunk(text_content)

        # Store chunks to database
        async with get_session("aiwen") as session:
            chunk_records = []
            for chunk in chunks:
                chunk_record = Chunk(
                    document_id=UUID(document_id),
                    user_id=UUID(user_id),
                    position=chunk.position,
                    content=chunk.content,
                    content_length=chunk.length,
                    status="pending",  # Pending embedding
                    enabled=True,
                    meta={
                        "start_char": chunk.start_char,
                        "end_char": chunk.end_char,
                    },
                )
                chunk_records.append(chunk_record)

            session.add_all(chunk_records)
            await session.flush()  # Get IDs

            for record in chunk_records:
                chunk_ids.append(str(record.id))

            # Update document with content preview
            await session.execute(
                update(Document)
                .where(Document.id == UUID(document_id))
                .values(
                    content=text_content[:1000] if text_content else None,
                    status="chunked",
                    chunk_count=len(chunk_records),
                )
            )

            await session.commit()
            logger.info(f"Stored {len(chunk_records)} chunks for document {document_id}")

        logger.info(
            f"Document {document_id} processed: {len(chunks)} chunks created "
            f"from {len(text_content)} characters"
        )

        return {
            "document_id": document_id,
            "user_id": user_id,
            "chunk_ids": chunk_ids,
            "total_chunks": len(chunks),
            "total_characters": len(text_content),
            "status": "chunked",
        }

    try:
        return run_async(_execute())
    except Exception as e:
        logger.error(f"Error in download_chunk_and_store for document {document_id}: {e}")

        async def _mark_failed():
            from aiwen.extensions.database import get_session
            from aiwen.models.agents.docments import Document
            from sqlalchemy import update
            if self.request.retries >= self.max_retries - 1:
                async with get_session("aiwen") as session:
                    await session.execute(
                        update(Document).where(Document.id == UUID(document_id)).values(
                            status="failed", error_message=str(e)
                        )
                    )
                    await session.commit()

        run_async(_mark_failed())
        self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="knowledge.embed_chunks",
    max_retries=3,
    default_retry_delay=60,
    queue="knowledge"
)
def embed_chunks(
        self,
        prev_result: dict[str, Any],
        embedding_provider: str = "tongyi",
        embedding_model: str = "text-embedding-v3",
        embedding_dimension: int = 1024,
) -> dict[str, Any]:
    """Generate embeddings for stored chunks and update them in database.

    Args:
        self: Celery task instance.
        prev_result: Output from download_chunk_and_store task.
        embedding_provider: Embedding provider (tongyi, openai, ollama).
        embedding_model: Embedding model name.
        embedding_dimension: Embedding vector dimension.

    Returns:
        dict: Processing result with status and chunk IDs.
    """
    document_id = prev_result.get("document_id")
    user_id = prev_result.get("user_id")
    chunk_ids = prev_result.get("chunk_ids", [])

    logger.info(
        f"Task embed_chunks started: document_id={document_id}, "
        f"chunks={len(chunk_ids)}, provider={embedding_provider}, model={embedding_model}"
    )

    if not chunk_ids:
        logger.info(f"No chunks to embed for document {document_id}")
        return {
            "document_id": document_id,
            "user_id": user_id,
            "chunk_ids": [],
            "status": "completed",
            "embedded_count": 0,
        }

    async def _execute():
        from aiwen.services.knowledge import EmbeddingService
        from aiwen.extensions.database import get_session
        from aiwen.models.agents.chunk import Chunk
        from aiwen.models.agents.docments import Document
        from sqlalchemy import select, update

        # Update document status
        async with get_session("aiwen") as session:
            await session.execute(
                update(Document).where(Document.id == UUID(document_id)).values(status="embedding")
            )
            await session.commit()

        # Initialize embedding service - sync
        embedding_service = EmbeddingService(
            provider=embedding_provider,
            model=embedding_model,
            dimension=embedding_dimension,
        )

        # Get embedding field name
        embedding_field = embedding_service.get_embedding_field_name()

        # Step 1: Fetch chunks from database
        valid_chunk_ids = []
        invalid_chunk_ids = []
        texts = []

        async with get_session("aiwen") as session:
            # Fetch chunks
            stmt = select(Chunk).where(
                Chunk.id.in_([UUID(cid) for cid in chunk_ids])
            ).order_by(Chunk.position)
            result = await session.execute(stmt)
            chunk_records = list(result.scalars().all())

            if not chunk_records:
                logger.warning(f"No chunks found for document {document_id}")
                return {
                    "document_id": document_id,
                    "user_id": user_id,
                    "chunk_ids": [],
                    "status": "completed",
                    "embedded_count": 0,
                }

            # Filter valid chunks with non-empty content
            for chunk in chunk_records:
                content = chunk.content
                logger.debug(f"Chunk {chunk.id}: content type={type(content)}, len={len(content) if content else 0}")
                if content and isinstance(content, str) and content.strip():
                    valid_chunk_ids.append(str(chunk.id))
                    # Ensure content is a proper string (not bytes or other)
                    texts.append(str(content))
                else:
                    logger.warning(f"Chunk {chunk.id} has invalid content: type={type(content)}, value={content!r}")
                    invalid_chunk_ids.append(str(chunk.id))

        # Step 2: Mark invalid chunks as failed (separate session)
        if invalid_chunk_ids:
            async with get_session("aiwen") as session:
                stmt = select(Chunk).where(Chunk.id.in_([UUID(cid) for cid in invalid_chunk_ids]))
                result = await session.execute(stmt)
                for chunk in result.scalars().all():
                    chunk.status = "failed"
                    chunk.meta = {
                        **(chunk.meta or {}),
                        "error": "Empty or invalid content",
                    }
                await session.commit()

        if not texts:
            logger.warning(f"No valid texts to embed for document {document_id}")
            return {
                "document_id": document_id,
                "user_id": user_id,
                "chunk_ids": [],
                "status": "completed",
                "embedded_count": 0,
            }

        # Step 3: Generate embeddings - sync operation OUTSIDE of any async session
        logger.info(f"Generating embeddings for {len(texts)} chunks")
        embeddings = embedding_service.embed_texts_batch(texts, batch_size=10)

        if len(embeddings) != len(valid_chunk_ids):
            logger.error(f"Embedding count mismatch: {len(embeddings)} embeddings for {len(valid_chunk_ids)} chunks")
            raise ValueError(f"Embedding count mismatch: got {len(embeddings)}, expected {len(valid_chunk_ids)}")

        # Step 4: Update chunks with embeddings (new session)
        async with get_session("aiwen") as session:
            stmt = select(Chunk).where(Chunk.id.in_([UUID(cid) for cid in valid_chunk_ids])).order_by(Chunk.position)
            result = await session.execute(stmt)
            chunk_records = list(result.scalars().all())

            # Create a mapping from chunk_id to embedding
            chunk_id_to_embedding = dict(zip(valid_chunk_ids, embeddings))

            for chunk_record in chunk_records:
                embedding = chunk_id_to_embedding.get(str(chunk_record.id))
                if embedding:
                    setattr(chunk_record, embedding_field, embedding)
                    chunk_record.status = "completed"
                    chunk_record.meta = {
                        **(chunk_record.meta or {}),
                        "embedding_model": embedding_model,
                        "embedding_provider": embedding_provider,
                    }

            await session.commit()
            logger.info(f"Updated {len(chunk_records)} chunks with embeddings for document {document_id}")

        # Update document status
        async with get_session("aiwen") as session:
            await session.execute(
                update(Document).where(Document.id == UUID(document_id)).values(status="embedded")
            )
            await session.commit()

        return {
            "document_id": document_id,
            "user_id": user_id,
            "chunk_ids": chunk_ids,
            "status": "embedded",
            "embedded_count": len(chunk_ids),
            "embedding_model": embedding_model,
            "embedding_provider": embedding_provider,
            "embedding_dimension": embedding_dimension,
        }

    try:
        return run_async(_execute())
    except Exception as e:
        logger.error(f"Error in embed_chunks for document {document_id}: {e}")

        async def _mark_failed():
            from aiwen.extensions.database import get_session
            from aiwen.models.agents.docments import Document
            from sqlalchemy import update
            if self.request.retries >= self.max_retries - 1:
                async with get_session("aiwen") as session:
                    await session.execute(
                        update(Document).where(Document.id == UUID(document_id)).values(
                            status="failed", error_message=str(e)
                        )
                    )
                    await session.commit()

        run_async(_mark_failed())
        self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="knowledge.link_chunks_to_context",
    max_retries=3,
    default_retry_delay=60,
    queue="knowledge"
)
def link_chunks_to_context(
        self,
        prev_result: dict[str, Any],
        knowledge_id: str,
) -> dict[str, Any]:
    """Create Context records from embedded chunks for knowledge base.

    Args:
        self: Celery task instance.
        prev_result: Output from embed_chunks task.
        knowledge_id: Knowledge base UUID (used as source_id in Context).

    Returns:
        dict: Processing result with status and context count.
    """
    document_id = prev_result.get("document_id")
    user_id = prev_result.get("user_id")
    chunk_ids = prev_result.get("chunk_ids", [])
    embedding_model = prev_result.get("embedding_model", "text-embedding-v3")
    embedding_provider = prev_result.get("embedding_provider", "tongyi")
    embedding_dimension = prev_result.get("embedding_dimension", 1024)

    logger.info(
        f"Task link_chunks_to_context started: document_id={document_id}, "
        f"knowledge_id={knowledge_id}, chunks={len(chunk_ids)}"
    )

    if not chunk_ids:
        logger.info(f"No chunks to link for document {document_id}")
        return {
            "document_id": document_id,
            "knowledge_id": knowledge_id,
            "status": "completed",
            "contexts_created": 0,
        }

    async def _execute():
        from aiwen.extensions.database import get_session
        from aiwen.models.agents.chunk import Chunk
        from aiwen.models.agents.context import Context
        from aiwen.models.agents.docments import Document
        from aiwen.schemas.agents.app import ContextType
        from sqlalchemy import select, update

        # Update document status
        async with get_session("aiwen") as session:
            await session.execute(
                update(Document).where(Document.id == UUID(document_id)).values(status="linking")
            )
            await session.commit()

        # Determine embedding field name
        embedding_field = f"embedding_{embedding_dimension}"

        contexts_created = 0

        async with get_session("aiwen") as session:
            # Fetch embedded chunks
            stmt = select(Chunk).where(
                Chunk.id.in_([UUID(cid) for cid in chunk_ids])
            ).order_by(Chunk.position)
            result = await session.execute(stmt)
            chunk_records = result.scalars().all()

            if not chunk_records:
                logger.warning(f"No chunks found for document {document_id}")
            else:
                # Create Context records
                context_records = []
                for chunk_record in chunk_records:
                    embedding = getattr(chunk_record, embedding_field, None)
                    context_record = Context(
                        user_id=UUID(user_id),
                        source_id=UUID(knowledge_id),
                        context_type=ContextType.CHUNK.value,
                        content=chunk_record.content,
                        meta={
                            "document_id": document_id,
                            "chunk_id": str(chunk_record.id),
                            "position": chunk_record.position,
                            "start_char": chunk_record.meta.get("start_char") if chunk_record.meta else None,
                            "end_char": chunk_record.meta.get("end_char") if chunk_record.meta else None,
                            "content_length": chunk_record.content_length,
                            "embedding_model": embedding_model,
                            "embedding_provider": embedding_provider,
                        },
                    )
                    if embedding is not None:
                        setattr(context_record, embedding_field, embedding)
                    context_records.append(context_record)

                session.add_all(context_records)
                await session.commit()
                contexts_created = len(context_records)
                logger.info(f"Created {contexts_created} contexts for document {document_id}")

        # Update document status to completed
        async with get_session("aiwen") as session:
            await session.execute(
                update(Document).where(Document.id == UUID(document_id)).values(status="completed")
            )
            await session.commit()

        return {
            "document_id": document_id,
            "knowledge_id": knowledge_id,
            "status": "completed",
            "contexts_created": contexts_created,
            "embedding_model": embedding_model,
            "embedding_provider": embedding_provider,
            "embedding_dimension": embedding_dimension,
        }

    try:
        return run_async(_execute())
    except Exception as e:
        logger.error(f"Error in link_chunks_to_context for document {document_id}: {e}")

        async def _mark_failed():
            from aiwen.extensions.database import get_session
            from aiwen.models.agents.docments import Document
            from sqlalchemy import update
            if self.request.retries >= self.max_retries - 1:
                async with get_session("aiwen") as session:
                    await session.execute(
                        update(Document).where(Document.id == UUID(document_id)).values(
                            status="failed", error_message=str(e)
                        )
                    )
                    await session.commit()

        run_async(_mark_failed())
        self.retry(exc=e)


def process_document_to_context(
        document_id: str,
        knowledge_id: str,
        bucket: str,
        object_key: str,
        mime_type: str,
        user_id: str,
        embedding_provider: str = "tongyi",
        embedding_model: str = "text-embedding-v3",
        embedding_dimension: int = 1024,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
) -> str:
    """Create and execute 3-task document processing chain.

    Task chain:
    1. download_chunk_and_store: Download, parse, chunk, store to Chunk table
    2. embed_chunks: Generate embeddings for chunks
    3. link_chunks_to_context: Create Context records for knowledge base

    Args:
        document_id: Document UUID.
        knowledge_id: Knowledge base UUID.
        bucket: MinIO bucket name.
        object_key: Object key in MinIO.
        mime_type: MIME type of the document.
        user_id: User UUID.
        embedding_provider: Embedding provider.
        embedding_model: Embedding model name.
        embedding_dimension: Embedding dimension.
        chunk_size: Chunk size for splitting.
        chunk_overlap: Overlap between chunks.

    Returns:
        str: Celery task chain ID.
    """
    task_chain = chain(
        download_chunk_and_store.s(
            document_id=document_id,
            user_id=user_id,
            bucket=bucket,
            object_key=object_key,
            mime_type=mime_type,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        ),
        embed_chunks.s(
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
        ),
        link_chunks_to_context.s(
            knowledge_id=knowledge_id,
        ),
    )

    try:
        result = task_chain.apply_async(queue="knowledge")
        logger.info(f"Started 3-task document processing chain: {result.id}")
        return result.id
    except Exception as e:
        logger.error(f"Failed to submit task chain: {e}")
        raise


# Legacy function for backward compatibility
def process_document_async(
        document_id: str,
        bucket: str,
        object_key: str,
        mime_type: str,
        user_id: str,
        embedding_provider: str = "tongyi",
        embedding_model: str = "text-embedding-v3",
        embedding_dimension: int = 1,
        chunk_size: int = 1024,
        chunk_overlap: int = 50,
) -> str:
    """Create and execute 2-task document processing chain (without Context linking).

    Task chain:
    1. download_chunk_and_store: Download, parse, chunk, store to Chunk table
    2. embed_chunks: Generate embeddings for chunks

    Args:
        document_id: Document UUID.
        bucket: MinIO bucket name.
        object_key: Object key in MinIO.
        mime_type: MIME type of the document.
        user_id: User UUID.
        embedding_provider: Embedding provider.
        embedding_model: Embedding model name.
        embedding_dimension: Embedding dimension.
        chunk_size: Chunk size for splitting.
        chunk_overlap: Overlap between chunks.

    Returns:
        str: Celery task chain ID.
    """
    task_chain = chain(
        download_chunk_and_store.s(
            document_id=document_id,
            user_id=user_id,
            bucket=bucket,
            object_key=object_key,
            mime_type=mime_type,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        ),
        embed_chunks.s(
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
        ),
    )

    result = task_chain.apply_async(queue="knowledge")
    logger.info(f"Started 2-task document processing chain: {result.id}")
    return result.id
