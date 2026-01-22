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
    name="documents.download_and_chunk",
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
    from aiwen.services.storage import MinioClient
    from aiwen.services.documents import DocumentParser, TextChunker
    from aiwen.services.documents.chunker import ChunkConfig

    logger.info(
        f"Task download_and_chunk started: document_id={document_id}, "
        f"bucket={bucket}, object_key={object_key}"
    )

    try:
        # Download file from MinIO
        minio_client = MinioClient()
        file_data = minio_client.download_file(bucket, object_key)

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
        self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="documents.embed_and_store",
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
    from aiwen.services.documents import EmbeddingService
    from aiwen.extensions.database import get_session
    from aiwen.models.agents.knowledge import Chunk, Document

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
