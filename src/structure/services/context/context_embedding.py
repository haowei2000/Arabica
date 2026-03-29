"""Centralized embedding helpers for the context system.

All context sync paths (Celery tasks, knowledge processing, registry sync)
should use these helpers instead of instantiating EmbeddingService directly.
This ensures consistent provider/model/dimension defaults across the system.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "tongyi"
DEFAULT_MODEL = "text-embedding-v3"
DEFAULT_DIMENSION = 1024


def embed_for_context(
    text: str,
    *,
    provider: str = DEFAULT_PROVIDER,
    model: str = DEFAULT_MODEL,
    dimension: int = DEFAULT_DIMENSION,
) -> tuple[list[float], str]:
    """Embed a single text for context storage; returns (vector, field_name).

    Must be called OUTSIDE any async DB session to avoid blocking the event loop.
    The returned ``field_name`` is the SQLAlchemy column to write to
    (e.g. ``"embedding_1024"``).
    """
    from structure.services.context.knowledge.embeddings import EmbeddingService

    svc = EmbeddingService(provider=provider, model=model, dimension=dimension)
    vector = svc.embed_text(text)
    field = svc.get_embedding_field_name()
    return vector, field


def embed_batch_for_context(
    texts: list[str],
    *,
    provider: str = DEFAULT_PROVIDER,
    model: str = DEFAULT_MODEL,
    dimension: int = DEFAULT_DIMENSION,
) -> tuple[list[list[float]], str]:
    """Embed multiple texts for context storage; returns (vectors, field_name).

    Must be called OUTSIDE any async DB session to avoid blocking the event loop.
    """
    from structure.services.context.knowledge.embeddings import EmbeddingService

    svc = EmbeddingService(provider=provider, model=model, dimension=dimension)
    vectors = svc.embed_texts(texts)
    field = svc.get_embedding_field_name()
    return vectors, field
