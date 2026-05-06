"""Shared SQL-backed context helpers for agent-facing context tools."""

from __future__ import annotations

import fnmatch
import json
import re
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.enums import ContextType, EventType
from structure.core.enums.context import ContextScope
from structure.models.context.context import Context
from structure.models.workspaces.workspace import Workspace
from structure.services.events.event_publisher import EventPublisher
from structure.services.workspace_context.workspace_context_service import (
    WorkspaceContextService,
)
from structure.utils.context import context_path_variants, semantic_context_path

DisclosureLevel = Literal["glance", "overview", "detail"]


def normalize_path(path: str) -> str:
    """Return the canonical DB path form used by agent context tools."""
    return semantic_context_path(path) or "/"


def normalize_level(level: str | None) -> DisclosureLevel:
    if level in ("glance", "overview", "detail"):
        return level
    return "overview"


def rating_avg(ctx: Context) -> float | None:
    return ctx.rating_avg


def passes_min_rating(ctx: Context, min_rating: float | None) -> bool:
    if min_rating is None:
        return True
    avg = rating_avg(ctx)
    return avg is not None and avg >= min_rating


def has_tags(ctx: Context, tags: list[str] | None) -> bool:
    if not tags:
        return True
    return bool(ctx.tags) and all(tag in (ctx.tags or []) for tag in tags)


def path_matches(ctx: Context, path: str) -> bool:
    variants = {variant.strip("/") for variant in context_path_variants(path)}
    return bool(ctx.path) and ctx.path.strip("/") in variants


def longest_static_prefix(pattern: str) -> str:
    """Return the non-glob prefix before the first wildcard."""
    idxs = [i for i in (pattern.find("*"), pattern.find("?")) if i >= 0]
    if not idxs:
        return pattern
    return pattern[: min(idxs)].rstrip("/")


def to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


async def resolve_owner_id(session: AsyncSession, workspace_id: UUID) -> UUID:
    workspace = await session.get(Workspace, workspace_id)
    if not workspace:
        raise ValueError(f"Workspace {workspace_id} not found")
    return workspace.owner_id


async def load_service(
    session: AsyncSession,
    workspace_id: UUID,
    user_id: str | None = None,
) -> WorkspaceContextService:
    owner_id = UUID(user_id) if user_id else None
    service = WorkspaceContextService(session, workspace_id, owner_id=owner_id)
    await service.load()
    return service


async def publish_context_event(
    session: AsyncSession,
    event_type: EventType,
    workspace_id: UUID,
    *,
    run_id: str | None = None,
    user_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    publisher = EventPublisher(db=session)
    await publisher.publish(
        event_type=event_type,
        workspace_id=workspace_id,
        run_id=run_id,
        user_id=user_id,
        payload=payload or {},
        auto_commit=False,
    )


async def publish_context_using(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    operation: str,
    paths: list[str],
    level: str,
    run_id: str | None = None,
    user_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    await publish_context_event(
        session,
        EventType.USING_CONTEXT,
        workspace_id,
        run_id=run_id,
        user_id=user_id,
        payload={
            "operation": operation,
            "paths": paths,
            "level": level,
            "count": len(paths),
            **(meta or {}),
        },
    )


async def find_context_by_path(
    session: AsyncSession,
    workspace_id: UUID,
    path: str,
    user_id: str | None = None,
) -> Context | None:
    service = await load_service(session, workspace_id, user_id)
    variants = {variant.strip("/") for variant in context_path_variants(path)}
    for ctx in service.contexts:
        if ctx.path and ctx.path.strip("/") in variants:
            return ctx
    return None


async def list_contexts(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    prefix: str | None = None,
    level: DisclosureLevel = "glance",
    user_id: str | None = None,
    recursive: bool = True,
    limit: int = 100,
    min_rating: float | None = None,
) -> list[dict[str, Any]]:
    service = await load_service(session, workspace_id, user_id)
    prefix_norm = prefix.strip("/") if prefix else ""
    prefix_depth = prefix_norm.count("/") if prefix_norm else -1
    items: list[dict[str, Any]] = []
    for ctx in service.contexts:
        if not ctx.path or not passes_min_rating(ctx, min_rating):
            continue
        path_norm = ctx.path.strip("/")
        if prefix_norm and not path_norm.startswith(prefix_norm):
            continue
        if not recursive and prefix_norm and path_norm != prefix_norm:
            rel = path_norm[len(prefix_norm) :].strip("/")
            if "/" in rel:
                continue
        elif not recursive and not prefix_norm and path_norm.count("/") > prefix_depth + 1:
            continue
        items.append(ctx.disclose(level))
        if len(items) >= limit:
            break
    return items


async def glob_contexts(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    pattern: str,
    level: DisclosureLevel = "glance",
    user_id: str | None = None,
    limit: int = 50,
    tags: list[str] | None = None,
    min_rating: float | None = None,
) -> list[dict[str, Any]]:
    service = await load_service(session, workspace_id, user_id)
    target = pattern.strip("/")
    static_prefix = longest_static_prefix(target).strip("/")
    items: list[dict[str, Any]] = []
    for ctx in service.contexts:
        if not ctx.path or not has_tags(ctx, tags) or not passes_min_rating(ctx, min_rating):
            continue
        path_norm = ctx.path.strip("/")
        if static_prefix and not path_norm.startswith(static_prefix):
            continue
        if fnmatch.fnmatch(path_norm, target):
            items.append(ctx.disclose(level))
            if len(items) >= limit:
                break
    return items


async def search_contexts(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    pattern: str,
    level: DisclosureLevel = "overview",
    user_id: str | None = None,
    limit: int = 10,
    context_type: str | None = None,
    ignore_case: bool = True,
    multiline: bool = False,
    min_rating: float | None = None,
) -> list[dict[str, Any]]:
    flags = 0
    if ignore_case:
        flags |= re.IGNORECASE
    if multiline:
        flags |= re.MULTILINE | re.DOTALL
    regex = re.compile(pattern, flags)

    service = await load_service(session, workspace_id, user_id)
    items: list[dict[str, Any]] = []
    for ctx in service.contexts:
        if context_type and ctx.context_type != context_type:
            continue
        if not passes_min_rating(ctx, min_rating):
            continue
        haystack = "\n".join(
            part for part in (ctx.path or "", ctx.glance or "", ctx.content or "") if part
        )
        if regex.search(haystack):
            items.append(ctx.disclose(level))
            if len(items) >= limit:
                break
    return items


async def query_contexts(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    query: str,
    prefix: str | None = None,
    level: DisclosureLevel = "overview",
    user_id: str | None = None,
    top_k: int = 10,
    min_rating: float | None = None,
    alpha: float = 0.7,
) -> list[dict[str, Any]]:
    """Hybrid lexical + rating query over scoped contexts.

    This deliberately avoids generating embeddings in the tool path. Existing
    embedding-backed API search remains available; this runtime tool provides a
    cheap, deterministic query surface for agent navigation.
    """
    service = await load_service(session, workspace_id, user_id)
    tokens = [t for t in re.split(r"\W+", query.lower()) if t]
    prefix_norm = prefix.strip("/") if prefix else ""
    alpha = max(0.0, min(1.0, alpha))
    scored: list[tuple[float, Context]] = []

    for ctx in service.contexts:
        if not ctx.path or not passes_min_rating(ctx, min_rating):
            continue
        path_norm = ctx.path.strip("/")
        if prefix_norm and not path_norm.startswith(prefix_norm):
            continue

        text = " ".join(
            part for part in (ctx.path or "", ctx.glance or "", ctx.content or "") if part
        ).lower()
        if tokens:
            hits = sum(1 for token in tokens if token in text)
            lexical = hits / len(tokens)
        else:
            lexical = 0.0
        if query.lower() in text:
            lexical += 0.25
        avg = rating_avg(ctx)
        rating_score = ((avg + 1.0) / 2.0) if avg is not None else 0.5
        score = alpha * lexical + (1.0 - alpha) * rating_score
        if score > 0:
            scored.append((score, ctx))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {**ctx.disclose(level), "score": round(score, 4)}
        for score, ctx in scored[:top_k]
    ]


async def upsert_workspace_context(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    path: str,
    glance: str | None,
    content: str,
    user_id: str | None = None,
    tags: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> tuple[Context, bool]:
    owner_id = UUID(user_id) if user_id else await resolve_owner_id(session, workspace_id)
    normalized = normalize_path(path)
    stmt = select(Context).where(
        Context.source_id == workspace_id,
        Context.scope == ContextScope.WORKSPACE,
        Context.path.in_(context_path_variants(normalized)),
    )
    result = await session.execute(stmt)
    ctx = result.scalar_one_or_none()
    created = ctx is None
    if ctx is None:
        ctx = Context(
            user_id=owner_id,
            source_id=workspace_id,
            scope=ContextScope.WORKSPACE,
            context_type=ContextType.WORKSPACE,
            path=normalized,
            glance=glance,
            content=content,
            tags=tags or [],
            meta={**(meta or {}), "workspace_id": str(workspace_id)},
        )
        session.add(ctx)
    else:
        content_changed = content != ctx.content
        ctx.glance = glance if glance is not None else ctx.glance
        ctx.content = content
        if tags is not None:
            ctx.tags = tags
        ctx.meta = {**(ctx.meta or {}), **(meta or {}), "workspace_id": str(workspace_id)}
        if content_changed:
            ctx.embedding_384 = None
            ctx.embedding_768 = None
            ctx.embedding_1024 = None
            ctx.embedding_1536 = None
    await session.flush()
    return ctx, created


async def delete_workspace_context(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    path: str,
    recursive: bool = False,
) -> int:
    normalized = normalize_path(path)
    variants = context_path_variants(normalized)
    conditions = [
        Context.source_id == workspace_id,
        Context.scope == ContextScope.WORKSPACE,
    ]
    if recursive:
        conditions.append(
            or_(
                Context.path.in_(variants),
                *(Context.path.like(f"{variant}/%") for variant in variants),
            )
        )
    else:
        conditions.append(Context.path.in_(variants))

    count_result = await session.execute(select(Context.id).where(*conditions))
    ids = [row[0] for row in count_result.all()]
    if not ids:
        return 0
    await session.execute(delete(Context).where(Context.id.in_(ids)))
    await session.flush()
    return len(ids)
