"""Celery tasks for document processing.

This module contains two chained tasks:
1. download_and_chunk: Download file from MinIO, parse, and split into chunks
2. embed_and_store: Generate embeddings and save chunks to database
"""

import asyncio
import logging
from typing import Any
from uuid import UUID

from celery import chain

from aiwen.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="knowledge.download_and_chunk",
    max_retries=3,
    default_retry_delay=60,
)
def download_and_chunk(
        self,
        document_id: str,
        bucket: str,
        object_key: str,
        mime_type: str,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
) -> dict[str, Any]:
    """Download file from MinIO, parse content, and split into chunks.

    Args:
        self: Celery task instance.
        document_id: Document UUID.
        bucket: MinIO bucket name.
        object_key: Object key/path in MinIO.
        mime_type: MIME type of the document.
        chunk_size: Maximum chunk size in characters.
        chunk_overlap: Overlap between chunks.

    Returns:
        dict: Contains document_id, user_id, and list of chunk data.
    """
    from aiwen.extensions.storage.global_storage import get_global_s3_storage
    from aiwen.services.knowledge import DocumentParser, TextChunker
    from aiwen.services.knowledge.chunker import ChunkConfig
    from aiwen.extensions.database import get_session
    from aiwen.models.agents.docments import Document
    from sqlalchemy import update

    logger.info(
        f"Task download_and_chunk started: document_id={document_id}, "
        f"bucket={bucket}, object_key={object_key}"
    )

    # Update document status to processing
    async def _update_status(status: str, error_message: str | None = None):
        async with get_session("aiwen") as session:
            values = {"status": status}
            if error_message:
                values["error_message"] = error_message
            await session.execute(
                update(Document).where(Document.id == UUID(document_id)).values(**values)
            )
            await session.commit()

    asyncio.run(_update_status("processing"))

    try:
        # Download file from S3 (RustFS)
        storage = get_global_s3_storage()
        file_data = storage.get_bytes(object_key)

        # Parse document content
        parser = DocumentParser()
        text_content = parser.parse(file_data, mime_type)

        if not text_content.strip():
            logger.warning(f"Document {document_id} has no extractable text content")
            return {
                "document_id": document_id,
                "chunks": [],
                "total_chunks": 0,
                "status": "empty",
            }

        # Split into chunks
        chunker_config = ChunkConfig(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        chunker = TextChunker(chunker_config)
        chunks = chunker.chunk(text_content)

        # Prepare chunk data for next task
        chunk_data = [
            {
                "content": chunk.content,
                "position": chunk.position,
                "content_length": chunk.length,
                "start_char": chunk.start_char,
                "end_char": chunk.end_char,
            }
            for chunk in chunks
        ]

        logger.info(
            f"Document {document_id} processed: {len(chunks)} chunks created "
            f"from {len(text_content)} characters"
        )

        return {
            "document_id": document_id,
            "chunks": chunk_data,
            "total_chunks": len(chunks),
            "total_characters": len(text_content),
            "status": "chunked",
        }

    except Exception as e:
        logger.error(f"Error in download_and_chunk for document {document_id}: {e}")
        # Update status to failed on final retry
        if self.request.retries >= self.max_retries - 1:
            asyncio.run(_update_status("failed", str(e)))
        self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="knowledge.embed_and_store",
    max_retries=3,
    default_retry_delay=60,
)
def embed_and_store(
        self,
        chunk_data: dict[str, Any],
        user_id: str,
        embedding_provider: str = "tongyi",
        embedding_model: str = "text-embedding-v3",
        embedding_dimension: int = 1536,
) -> dict[str, Any]:
    """Generate embeddings for chunks and store in database.

    Args:
        self: Celery task instance.
        chunk_data: Output from download_and_chunk task.
        user_id: User UUID who owns the document.
        embedding_provider: Embedding provider (tongyi, openai, ollama).
        embedding_model: Embedding model name.
        embedding_dimension: Embedding vector dimension.

    Returns:
        dict: Processing result with status and chunk count.
    """
    from aiwen.services.knowledge import EmbeddingService
    from aiwen.extensions.database import get_session
    from aiwen.models.agents import Chunk, Document

    logger.info(
        f"Task embed_and_store started: document_id={chunk_data.get('document_id')}, "
        f"provider={embedding_provider}, model={embedding_model}"
    )

    document_id = chunk_data.get("document_id")
    chunks = chunk_data.get("chunks", [])

    if not chunks:
        logger.info(f"No chunks to process for document {document_id}")
        return {
            "document_id": document_id,
            "status": "completed",
            "chunks_created": 0,
        }

    try:
        # Initialize embedding service
        embedding_service = EmbeddingService(
            provider=embedding_provider,
            model=embedding_model,
            dimension=embedding_dimension,
        )

        # Get embedding field name
        embedding_field = embedding_service.get_embedding_field_name()

        # Generate embeddings for all chunks
        texts = [chunk["content"] for chunk in chunks]
        embeddings = embedding_service.embed_texts_batch(texts, batch_size=50)

        # Store chunks with embeddings in database
        async def _store_chunks():
            async with get_session("aiwen") as session:
                # Update document status
                from sqlalchemy import select, update

                # Create chunk records
                chunk_records = []
                for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                    chunk_record = Chunk(
                        document_id=UUID(document_id),
                        user_id=UUID(user_id),
                        position=chunk["position"],
                        content=chunk["content"],
                        content_length=chunk["content_length"],
                        status="completed",
                        enabled=True,
                        meta={
                            "start_char": chunk["start_char"],
                            "end_char": chunk["end_char"],
                            "embedding_model": embedding_model,
                            "embedding_provider": embedding_provider,
                        },
                    )
                    # Set the appropriate embedding field
                    setattr(chunk_record, embedding_field, embedding)
                    chunk_records.append(chunk_record)

                session.add_all(chunk_records)

                # Update document chunk count
                await session.execute(
                    update(Document)
                    .where(Document.id == UUID(document_id))
                    .values(content=texts[0][:1000] if texts else None)  # Store first 1000 chars as preview
                )

                await session.commit()
                logger.info(f"Stored {len(chunk_records)} chunks for document {document_id}")

        asyncio.run(_store_chunks())

        return {
            "document_id": document_id,
            "status": "completed",
            "chunks_created": len(chunks),
            "embedding_model": embedding_model,
            "embedding_provider": embedding_provider,
            "embedding_dimension": embedding_dimension,
        }

    except Exception as e:
        logger.error(f"Error in embed_and_store for document {document_id}: {e}")
        self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="knowledge.embed_and_store_context",
    max_retries=3,
    default_retry_delay=60,
)
def embed_and_store_context(
        self,
        chunk_data: dict[str, Any],
        user_id: str,
        knowledge_id: str,
        document_id: str,
        embedding_provider: str = "tongyi",
        embedding_model: str = "text-embedding-v3",
        embedding_dimension: int = 1536,
) -> dict[str, Any]:
    """Generate embeddings for chunks and store in Context table.

    Args:
        self: Celery task instance.
        chunk_data: Output from download_and_chunk task.
        user_id: User UUID who owns the document.
        knowledge_id: Knowledge base UUID (used as source_id in Context).
        document_id: Document UUID.
        embedding_provider: Embedding provider (tongyi, openai, ollama).
        embedding_model: Embedding model name.
        embedding_dimension: Embedding vector dimension.

    Returns:
        dict: Processing result with status and chunk count.
    """
    from aiwen.services.knowledge import EmbeddingService
    from aiwen.extensions.database import get_session
    from aiwen.models.agents.context import Context
    from aiwen.models.agents.docments import Document
    from aiwen.schemas.agents.app import ContextType
    from sqlalchemy import update

    logger.info(
        f"Task embed_and_store_context started: document_id={chunk_data.get('document_id')}, "
        f"knowledge_id={knowledge_id}, provider={embedding_provider}, model={embedding_model}"
    )

    # Helper to update document status
    async def _update_document_status(status: str, chunk_count: int = 0, error_message: str | None = None):
        async with get_session("aiwen") as session:
            values = {"status": status, "chunk_count": chunk_count}
            if error_message:
                values["error_message"] = error_message
            await session.execute(
                update(Document).where(Document.id == UUID(document_id)).values(**values)
            )
            await session.commit()

    chunks = chunk_data.get("chunks", [])

    if not chunks:
        logger.info(f"No chunks to process for document {document_id}")
        asyncio.run(_update_document_status("completed", 0))
        return {
            "document_id": document_id,
            "knowledge_id": knowledge_id,
            "status": "completed",
            "contexts_created": 0,
        }

    try:
        # Initialize embedding service
        embedding_service = EmbeddingService(
            provider=embedding_provider,
            model=embedding_model,
            dimension=embedding_dimension,
        )

        # Get embedding field name
        embedding_field = embedding_service.get_embedding_field_name()

        # Generate embeddings for all chunks
        texts = [chunk["content"] for chunk in chunks]

        # Debug: log text info
        logger.debug(f"Total chunks: {len(chunks)}, texts to embed: {len(texts)}")
        for i, text in enumerate(texts[:3]):  # Log first 3 for debugging
            logger.debug(
                f"Text {i}: type={type(text)}, len={len(text) if text else 'None'}, preview={repr(text[:50]) if text else 'None'}")

        embeddings = embedding_service.embed_texts_batch(texts, batch_size=50)

        # Store chunks as Context records in database
        async def _store_contexts():
            async with get_session("aiwen") as session:
                context_records = []
                for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                    context_record = Context(
                        user_id=UUID(user_id),
                        source_id=UUID(knowledge_id),
                        context_type=ContextType.CHUNK.value,
                        content=chunk["content"],
                        meta={
                            "document_id": document_id,
                            "position": chunk["position"],
                            "start_char": chunk["start_char"],
                            "end_char": chunk["end_char"],
                            "content_length": chunk["content_length"],
                            "embedding_model": embedding_model,
                            "embedding_provider": embedding_provider,
                        },
                    )
                    # Set the appropriate embedding field
                    setattr(context_record, embedding_field, embedding)
                    context_records.append(context_record)

                session.add_all(context_records)
                await session.commit()
                logger.info(f"Stored {len(context_records)} contexts for document {document_id}")

        asyncio.run(_store_contexts())

        # Update document status to completed with chunk count
        asyncio.run(_update_document_status("completed", len(chunks)))

        return {
            "document_id": document_id,
            "knowledge_id": knowledge_id,
            "status": "completed",
            "contexts_created": len(chunks),
            "embedding_model": embedding_model,
            "embedding_provider": embedding_provider,
            "embedding_dimension": embedding_dimension,
        }

    except Exception as e:
        logger.error(f"Error in embed_and_store_context for document {document_id}: {e}")
        # Update status to failed on final retry
        if self.request.retries >= self.max_retries - 1:
            asyncio.run(_update_document_status("failed", 0, str(e)))
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
        embedding_dimension: int = 1536,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
) -> str:
    """Create and execute document processing task chain that stores in Context table.

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
        download_and_chunk.s(
            document_id=document_id,
            bucket=bucket,
            object_key=object_key,
            mime_type=mime_type,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        ),
        embed_and_store_context.s(
            user_id=user_id,
            knowledge_id=knowledge_id,
            document_id=document_id,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
        ),
    )

    try:
        result = task_chain.apply_async()
        logger.info(f"Started document to context processing chain: {result.id}")
        return result.id
    except Exception as e:
        logger.error(f"Failed to submit task chain: {e}")
        raise


def process_document_async(
        document_id: str,
        bucket: str,
        object_key: str,
        mime_type: str,
        user_id: str,
        embedding_provider: str = "tongyi",
        embedding_model: str = "text-embedding-v3",
        embedding_dimension: int = 1536,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
) -> str:
    """Create and execute document processing task chain.

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
        download_and_chunk.s(
            document_id=document_id,
            bucket=bucket,
            object_key=object_key,
            mime_type=mime_type,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        ),
        embed_and_store.s(
            user_id=user_id,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
        ),
    )

    result = task_chain.apply_async()
    logger.info(f"Started document processing chain: {result.id}")
    return result.id
