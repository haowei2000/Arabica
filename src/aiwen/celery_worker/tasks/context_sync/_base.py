"""Shared helpers and constants for context-sync Celery tasks."""

import logging
from uuid import UUID

from aiwen.services.context.context_embedding import (
    DEFAULT_DIMENSION as _DEFAULT_DIMENSION,
    DEFAULT_MODEL as _DEFAULT_MODEL,
    DEFAULT_PROVIDER as _DEFAULT_PROVIDER,
    embed_for_context as _embed_for_context,
)

logger = logging.getLogger(__name__)



async def _upsert_context(session, *, user_id: str, context_type: str, source_id: str,
                           glance: str | None,
                           content: str | None, tags: list[str] | None = None, path: str | None = None,
                           meta: dict | None = None) -> tuple:
    """Create or update a Context row identified by (source_id, context_type, user_id).

    Returns ``(ctx, needs_embedding)`` where ``needs_embedding`` is True only
    when the row is newly created or its ``content`` field has changed.
    Delegates to ContextCRUD.upsert_by_source.
    """
    from aiwen.services.context.context_crud import ContextCRUD

    crud = ContextCRUD(session)
    return await crud.upsert_by_source(
        source_id=source_id,
        context_type=context_type,
        user_id=user_id,
        data={
            "glance": glance,
            "content": content,
            "path": path,
            "tags": tags,
            "meta": meta,
        },
    )


async def _upsert_context_at_path(session, *, user_id: str, context_type: str, source_id: str,
                                   path: str, glance: str | None, content: str | None,
                                   tags: list[str] | None = None,
                                   meta: dict | None = None) -> tuple:
    """Create or update a Context row identified by (source_id, context_type, user_id, path).

    Unlike ``_upsert_context``, the ``path`` is part of the unique key so multiple
    entries with different paths can coexist for the same source.
    Delegates to ContextCRUD.upsert_by_source_and_path.
    """
    from aiwen.services.context.context_crud import ContextCRUD

    crud = ContextCRUD(session)
    return await crud.upsert_by_source_and_path(
        source_id=source_id,
        context_type=context_type,
        user_id=user_id,
        path=path,
        data={
            "glance": glance,
            "content": content,
            "tags": tags,
            "meta": meta,
        },
    )


def _generate_embedding(text: str, *,
                         provider: str = _DEFAULT_PROVIDER,
                         model: str = _DEFAULT_MODEL,
                         dimension: int = _DEFAULT_DIMENSION) -> tuple[list[float], str]:
    """Embed text synchronously; returns (vector, field_name).

    Must be called OUTSIDE any async DB session to avoid blocking the event loop.
    Delegates to the centralized context_embedding module.
    """
    return _embed_for_context(text, provider=provider, model=model, dimension=dimension)


async def _store_embedding(session, ctx_id: str, vector: list[float], field: str):
    """Write the embedding vector to an existing Context row."""
    from aiwen.models.context.context import Context

    ctx = await session.get(Context, UUID(ctx_id))
    if ctx is not None:
        setattr(ctx, field, vector)
