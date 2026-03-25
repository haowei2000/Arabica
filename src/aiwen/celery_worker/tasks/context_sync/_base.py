"""Shared helpers and constants for context-sync Celery tasks."""

import re
import logging
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

_DEFAULT_PROVIDER = "tongyi"
_DEFAULT_MODEL = "text-embedding-v3"
_DEFAULT_DIMENSION = 1024





async def _upsert_context(session, *, user_id: str, context_type: str, source_id: str,
                           glance: str | None,
                           content: str | None, tags: list[str] | None = None, path: str | None = None,
                           meta: dict | None = None) -> tuple:
    """Create or update a Context row identified by (source_id, context_type, user_id).

    Returns ``(ctx, needs_embedding)`` where ``needs_embedding`` is True only
    when the row is newly created or its ``content`` field has changed.
    Embeddings are cleared only in those cases to avoid redundant re-embedding
    on metadata-only updates (glance, tags, meta).
    """
    from sqlalchemy import select

    from aiwen.models.context.context import Context

    merged_meta = meta or {}

    stmt = select(Context).where(
        Context.source_id == UUID(source_id),
        Context.context_type == context_type,
        Context.user_id == UUID(user_id),
    )
    result = await session.execute(stmt)
    ctx = result.scalar_one_or_none()

    if ctx:
        new_content = content or ctx.content
        content_changed = new_content != ctx.content

        ctx.glance = glance
        ctx.content = new_content
        if tags is not None:
            ctx.tags = tags
        ctx.meta = {**(ctx.meta or {}), **merged_meta}

        if content_changed:
            ctx.embedding_384 = None
            ctx.embedding_768 = None
            ctx.embedding_1024 = None
            ctx.embedding_1536 = None

        return ctx, content_changed
    else:
        ctx = Context(
            id=uuid4(),
            user_id=UUID(user_id),
            context_type=context_type,
            source_id=UUID(source_id),
            glance=glance,
            path=path,
            content=content or "",
            tags=tags or [],
            meta=merged_meta,
        )
        session.add(ctx)
        return ctx, True


def _generate_embedding(text: str, *,
                         provider: str = _DEFAULT_PROVIDER,
                         model: str = _DEFAULT_MODEL,
                         dimension: int = _DEFAULT_DIMENSION) -> tuple[list[float], str]:
    """Embed text synchronously; returns (vector, field_name).

    Must be called OUTSIDE any async DB session to avoid blocking the event loop.
    """
    from aiwen.services.context.knowledge.embeddings import EmbeddingService

    svc = EmbeddingService(provider=provider, model=model, dimension=dimension)
    vector = svc.embed_text(text)
    field = svc.get_embedding_field_name()
    return vector, field


async def _store_embedding(session, ctx_id: str, vector: list[float], field: str):
    """Write the embedding vector to an existing Context row."""
    from aiwen.models.context.context import Context

    ctx = await session.get(Context, UUID(ctx_id))
    if ctx is not None:
        setattr(ctx, field, vector)
