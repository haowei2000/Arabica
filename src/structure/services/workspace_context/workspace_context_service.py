"""Workspace context service — read-only query facade over multi-scope Context.

Simplified implementation that uses Context ORM models directly instead of
ContextStore/ContextEntry abstractions.
"""

from __future__ import annotations

from datetime import UTC, datetime
import fnmatch
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.enums.context import ContextScope
from structure.models.context.context import Context
from structure.models.context.workspace_context import WorkspaceContext

logger = logging.getLogger(__name__)


class WorkspaceContextService:
    """Read-only query facade over Context (Workspace + User + Global scopes).

    Usage::

        service = WorkspaceContextService(session, workspace_id)
        await service.load()

        tools = await service.glob("tools/*")
        entry = await service.get("tools/web_search", "detail")
    """

    def __init__(
        self,
        session: AsyncSession,
        workspace_id: str | UUID,
        owner_id: str | UUID | None = None,
    ) -> None:
        self.session = session
        self.workspace_id = str(workspace_id)
        self._workspace_uuid = UUID(self.workspace_id)
        self.owner_id = str(owner_id) if owner_id else None
        self._owner_uuid = UUID(self.owner_id) if self.owner_id else None

        self._contexts: list[Context] = []
        self._workspace_contexts: list[WorkspaceContext] = []
        self._loaded = False

    async def _ensure_owner_id(self) -> None:
        """Fetch workspace owner_id from DB if not provided."""
        if self._owner_uuid:
            return
        from structure.models.workspaces.workspace import Workspace

        ws = await self.session.get(Workspace, self._workspace_uuid)
        if ws:
            self.owner_id = str(ws.owner_id)
            self._owner_uuid = ws.owner_id

    async def load(self) -> list[Context]:
        """Load tool-visible context rows from DB into memory.

        The durable ``context`` table stores user/global/workspace-scoped
        knowledge, while the ``workspace_context`` table stores entries created
        through workspace APIs such as context copy/upload.  Agent tools should
        see both.
        """
        await self._ensure_owner_id()

        # Multi-scope load: Workspace-specific + User-scoped (owner) + Global
        stmt = (
            select(Context)
            .where(
                or_(
                    and_(
                        Context.source_id == self._workspace_uuid,
                        Context.scope == ContextScope.WORKSPACE,
                    ),
                    and_(
                        Context.user_id == self._owner_uuid,
                        Context.scope == ContextScope.USER,
                    ),
                    Context.scope == ContextScope.GLOBAL,
                )
            )
            .order_by(Context.path)
        )
        result = await self.session.execute(stmt)
        self._contexts = list(result.scalars().all())

        workspace_stmt = (
            select(WorkspaceContext)
            .where(
                WorkspaceContext.workspace_id == self._workspace_uuid,
                WorkspaceContext.is_deleted.is_(False),
                or_(
                    WorkspaceContext.expires_at.is_(None),
                    WorkspaceContext.expires_at > datetime.now(UTC),
                ),
            )
            .order_by(WorkspaceContext.path)
        )
        workspace_result = await self.session.execute(workspace_stmt)
        self._workspace_contexts = list(workspace_result.scalars().all())
        self._loaded = True

        logger.debug(
            "WorkspaceContextService: loaded %d Context rows and %d "
            "WorkspaceContext rows for workspace %s",
            len(self._contexts),
            len(self._workspace_contexts),
            self.workspace_id,
        )
        return self._contexts

    async def _ensure_loaded(self) -> None:
        if not self._loaded:
            await self.load()

    def _normalize_path(self, path: str) -> str:
        return path.strip("/")

    def _iter_visible_contexts(self):
        """Yield merged contexts, preferring workspace-local rows by path."""
        seen: set[str] = set()
        for ctx in (*self._workspace_contexts, *self._contexts):
            if not ctx.path:
                continue
            path = self._normalize_path(ctx.path)
            if path in seen:
                continue
            seen.add(path)
            yield ctx

    # ──────────────────────────────────────────────────────────────
    # Query API
    # ──────────────────────────────────────────────────────────────

    async def get(self, path: str, level: str = "overview") -> dict[str, Any] | None:
        """Get a single context by path."""
        await self._ensure_loaded()
        target = self._normalize_path(path)

        for ctx in self._iter_visible_contexts():
            if self._normalize_path(ctx.path) == target:
                return {"path": ctx.path, **ctx.disclose(level)}
        return None

    async def glob(self, pattern: str, level: str = "overview") -> list[dict[str, Any]]:
        """Query contexts using glob patterns (e.g. 'tools/*', 'knowledge/**')."""
        await self._ensure_loaded()
        target_pat = self._normalize_path(pattern)

        results = []
        for ctx in self._iter_visible_contexts():
            path = self._normalize_path(ctx.path)
            if fnmatch.fnmatch(path, target_pat):
                results.append({"path": ctx.path, **ctx.disclose(level)})
        return results

    async def list(
        self, prefix: str | None = None, level: str = "glance"
    ) -> list[dict[str, Any]]:
        """List contexts, optionally filtered by path prefix."""
        await self._ensure_loaded()

        results = []
        prefix_norm = self._normalize_path(prefix) if prefix else ""

        for ctx in self._iter_visible_contexts():
            path = self._normalize_path(ctx.path)
            if not prefix_norm or path.startswith(prefix_norm):
                results.append({"path": ctx.path, **ctx.disclose(level)})
        return results

    async def tree(
        self, root: str | None = None, level: str = "overview"
    ) -> dict[str, Any]:
        """Return context structure as a hierarchical tree."""
        await self._ensure_loaded()

        all_items = await self.list(root, level)
        if not all_items:
            return {}

        # Simple tree construction
        nodes_by_path: dict[str, dict[str, Any]] = {}
        roots: list[dict[str, Any]] = []

        for item in sorted(all_items, key=lambda x: x["path"]):
            path = item["path"]
            node = {**item, "children": []}
            path_key = self._normalize_path(path)
            nodes_by_path[path_key] = node

            # Find parent
            parts = path_key.rsplit("/", 1)
            parent_path = parts[0] if len(parts) > 1 else None

            if parent_path and parent_path in nodes_by_path:
                nodes_by_path[parent_path]["children"].append(node)
            else:
                roots.append(node)

        if len(roots) == 1:
            return roots[0]
        return {"path": root or "/", "children": roots}

    @property
    def contexts(self) -> list[Context]:
        """Raw Context model instances."""
        return self._contexts

    @property
    def workspace_contexts(self) -> list[WorkspaceContext]:
        """Raw WorkspaceContext model instances."""
        return self._workspace_contexts
