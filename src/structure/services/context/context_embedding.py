"""Centralized embedding helpers for the context system.

All context sync paths (Celery tasks, knowledge processing, registry sync)
should use these helpers instead of instantiating EmbeddingService directly.
This ensures consistent provider/model/dimension defaults across the system.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from structure.services.context.knowledge.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "tongyi"
DEFAULT_MODEL = "text-embedding-v3"
DEFAULT_DIMENSION = 1024


async def load_default_embedding_service(db: AsyncSession) -> EmbeddingService:
    """Fetch the default EmbeddingModel from DB and return a configured EmbeddingService.

    Must be awaited inside an async context BEFORE calling embed_for_context/embed_batch_for_context
    (which must run outside any DB session).
    """
    from structure.services.context.knowledge.embeddings import EmbeddingService
    from structure.services.llm.embedding_model_crud import EmbeddingModelCRUD

    model = await EmbeddingModelCRUD(db).get_default()
    if model:
        return EmbeddingService(
            provider=model.provider,  # type: ignore[arg-type]
            model=model.model_id,
            dimension=model.dimension,
            api_key=model.api_key_ref or "",
            base_url=model.base_url or "",
        )
    # Fallback: return an unconfigured service that will raise on use
    return EmbeddingService(
        provider=DEFAULT_PROVIDER,
        model=DEFAULT_MODEL,
        dimension=DEFAULT_DIMENSION,
    )


def embed_for_context(
    text: str,
    svc: EmbeddingService,
) -> tuple[list[float], str]:
    """Embed a single text using a pre-built EmbeddingService; returns (vector, field_name).

    Must be called OUTSIDE any async DB session to avoid blocking the event loop.
    Call ``load_default_embedding_service(db)`` first to obtain ``svc``.
    """
    vector = svc.embed_text(text)
    field = svc.get_embedding_field_name()
    return vector, field


def embed_batch_for_context(
    texts: list[str],
    svc: EmbeddingService,
) -> tuple[list[list[float]], str]:
    """Embed multiple texts using a pre-built EmbeddingService; returns (vectors, field_name).

    Must be called OUTSIDE any async DB session to avoid blocking the event loop.
    """
    vectors = svc.embed_texts(texts)
    field = svc.get_embedding_field_name()
    return vectors, field
