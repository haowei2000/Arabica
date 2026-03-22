"""Workspace context service — read-only query facade over Context.

Architecture
============
The write path is intentionally simple:

1. **Global Context table** — single source of truth, kept up to date by
   ``ContextSyncer`` whenever tools / skills / knowledge / triggers / workspaces
   are created, updated, or deleted.

2. **Workspace-scoped Context entries** — Context rows with
   ``source_id == workspace_id``, created at workspace creation time
   (see ``init_workspace_context``).  After creation they are not modified.

3. **ContextStore (in-memory)** — loaded on demand from Context for
   fast glob / tree / children queries within a single request.

WorkspaceContextService exposes read-only query methods.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.core.enums.context import ContextScope
from aiwen.models.context.context import Context


class WorkspaceContextService:
    """Read-only query facade over Context (workspace-scoped) + in-memory ContextStore.

    Usage::

        service = WorkspaceContextService(session, workspace_id)
        await service.load()

        tools = await service.glob("tools/**")
        tree  = await service.tree("tools")
        entry = await service.get("tools/web_search", "overview")
    """

    def __init__(self, session: AsyncSession, workspace_id: str | UUID) -> None:
        self.session = session
        self.workspace_id = str(workspace_id)
        self._workspace_uuid = UUID(self.workspace_id)
        self._store = None
        self._loaded = False

    # ──────────────────────────────────────────────────────────────
    # Loading
    # ──────────────────────────────────────────────────────────────

    async def load(self):  # -> ContextStore
        """Load workspace-scoped Context rows from DB into the in-memory ContextStore."""
        from aiwen.frameworks.context import ContextStore, count_aggregator
        from aiwen.core.enums.context import ContextPathSuffix

        if self._store is None:
            self._store = ContextStore(
                title=f"Workspace {self.workspace_id}",
                description=f"Context snapshot for workspace {self.workspace_id}",
            )

            # Register standard schema nodes (skeleton / directory nodes)
            paths = {
                ContextPathSuffix.TOOLS: "Available Tools",
                ContextPathSuffix.SKILLS: "Available Skills",
                ContextPathSuffix.KNOWLEDGE: "Knowledge Base",
                ContextPathSuffix.TRIGGERS: "Triggers",
                ContextPathSuffix.WORKSPACES: "Workspaces",
                ContextPathSuffix.LONG_MEMORY: "Long Memory",
                ContextPathSuffix.SHORT_MEMORY: "Short Memory",
            }
            for suffix, title in paths.items():
                self._store.schema(
                    suffix,
                    glance=title,
                    aggregator=count_aggregator,
                    tags=["workspace", suffix],
                )

        stmt = (
            select(Context)
            .where(
                Context.source_id == self._workspace_uuid,
                Context.scope == ContextScope.WORKSPACE,
            )
            .order_by(Context.path)
        )
        result = await self.session.execute(stmt)
        for ctx in result.scalars().all():
            self._load_entry(ctx)

        self._loaded = True
        return self._store

    def _load_entry(self, ctx: Context) -> None:
        path = ctx.path.lstrip("/") if ctx.path else f"_/{ctx.id}"

        overview = ctx.summary
        if overview and isinstance(overview, str) and overview.startswith("{"):
            with contextlib.suppress(json.JSONDecodeError, ValueError):
                overview = json.loads(overview)

        meta = ctx.meta or {}
        meta.update(
            {
                "id": str(ctx.id),
                "workspace_id": self.workspace_id,
                "s3_key": ctx.s3_key,
            }
        )

        self._store.set(
            path=path,
            glance=ctx.glance or ctx.summary or ctx.content[:50] if ctx.content else path,
            overview=overview,
            detail=ctx.content,
            tags=ctx.tags or [],
            meta=meta,
        )

    async def _ensure_loaded(self) -> None:
        if not self._loaded:
            await self.load()

    # ──────────────────────────────────────────────────────────────
    # Query API (read-only)
    # ──────────────────────────────────────────────────────────────

    async def get(self, path: str, level: str = "overview") -> Any:
        await self._ensure_loaded()
        from aiwen.frameworks.context import DetailLevel

        return self._store.get(path, DetailLevel.from_str(level))

    async def glob(self, pattern: str):  # -> QueryResult
        await self._ensure_loaded()
        return self._store.glob(pattern)

    async def children(self, prefix: str):  # -> QueryResult
        await self._ensure_loaded()
        return self._store.children(prefix)

    async def descendants(self, prefix: str):  # -> QueryResult
        await self._ensure_loaded()
        return self._store.descendants(prefix)

    async def tree(self, root: str | None = None, level: str = "overview") -> Any:
        await self._ensure_loaded()
        from aiwen.frameworks.context import DetailLevel

        return self._store.tree(root, DetailLevel.from_str(level))

    async def glance(self, prefix: str | None = None) -> list[str]:
        await self._ensure_loaded()
        return self._store.glance(prefix)

    @property
    def store(self):
        """Raw ContextStore instance (read-only — do not modify directly)."""
        return self._store
