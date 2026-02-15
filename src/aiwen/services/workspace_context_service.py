"""Workspace context service with ContextStore caching and persistence.

Architecture:
    WorkspaceContext (DB) ←→ ContextStore (Memory Cache)
    - WorkspaceContext: Persistent storage (survives restarts)
    - ContextStore: Fast in-memory queries (glob, tree, etc.)
    - Auto-sync: Changes to ContextStore sync back to DB
"""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.context.workspace_context import WorkspaceContext
from aiwen.plugins.executors.conflict.prompts import (
    ContextPathSuffix,
    build_context_path,
)


class WorkspaceContextService:
    """Workspace context service with persistent storage + memory cache.

    Features:
    - Fast queries using ContextStore (glob, children, tree)
    - Persistent storage in WorkspaceContext table
    - Auto-sync on modifications
    - Survives server restarts

    Usage:
        service = WorkspaceContextService(session, workspace_id)

        # Load from DB on startup
        await service.load()

        # Fast queries using ContextStore API
        tools = service.glob("tools/**")
        tree = service.tree("tools")

        # Modifications auto-sync to DB
        await service.set("tools/new_tool", glance="New Tool", ...)

        # Restore after restart
        await service.load()  # Loads from DB
    """

    def __init__(self, session: AsyncSession, workspace_id: str | UUID):
        """Initialize service.

        Args:
            session: Database session
            workspace_id: Workspace ID
        """
        self.session = session
        self.workspace_id = str(workspace_id)
        self._store = None  # Lazy loaded
        self._loaded = False

    async def load(self):  # -> ContextStore:
        """Load workspace contexts from DB into memory cache.

        Call this:
        - On workspace creation
        - On server restart
        - When workspace context needs refresh

        Returns:
            ContextStore instance
        """
        from aiwen.frameworks.context_layer import (
            ContextStore,
            count_aggregator,
        )

        if self._store is None:
            self._store = ContextStore(
                title=f"Workspace {self.workspace_id}",
                description=f"Context cache for workspace {self.workspace_id}"
            )

            # Register schema nodes
            await self._register_schemas()

        # Load all workspace contexts from DB
        stmt = select(WorkspaceContext).where(
            WorkspaceContext.workspace_id == self.workspace_id,
            WorkspaceContext.is_deleted == False,  # noqa: E712
        ).order_by(WorkspaceContext.path)

        result = await self.session.execute(stmt)
        contexts = result.scalars().all()

        # Load into ContextStore
        for ctx in contexts:
            self._load_entry(ctx)

        self._loaded = True
        return self._store

    async def _register_schemas(self):
        """Register schema nodes for workspace structure."""
        from aiwen.frameworks.context_layer import count_aggregator

        # Root workspace
        self._store.schema(
            self.workspace_id,
            glance=f"Workspace {self.workspace_id}",
            aggregator=count_aggregator,
            tags=["workspace"]
        )

        # Standard paths
        paths = {
            ContextPathSuffix.TOOLS: "Available Tools",
            ContextPathSuffix.SKILLS: "Available Skills",
            ContextPathSuffix.KNOWLEDGE: "Knowledge Base",
            ContextPathSuffix.WORKSPACE_HISTORY: "Run History",
        }

        for suffix, title in paths.items():
            path = build_context_path(self.workspace_id, suffix)
            self._store.schema(
                path.lstrip("/"),
                glance=title,
                aggregator=count_aggregator,
                tags=["workspace", suffix]
            )

    def _load_entry(self, ctx: WorkspaceContext):
        """Load WorkspaceContext into ContextStore."""
        path = ctx.path.lstrip("/") if ctx.path else f"_/{ctx.id}"

        # Parse overview
        overview = ctx.summary
        if overview and overview.startswith("{"):
            try:
                import json
                overview = json.loads(overview)
            except (json.JSONDecodeError, ValueError):
                pass

        # Build meta
        meta = ctx.meta or {}
        meta.update({
            "id": str(ctx.id),
            "workspace_id": str(ctx.workspace_id),
            "created_by": str(ctx.created_by) if ctx.created_by else None,
            "s3_key": ctx.s3_key,
            "content_type": ctx.content_type,
            "size_bytes": ctx.size_bytes,
        })

        self._store.set(
            path=path,
            glance=ctx.glance or ctx.summary or ctx.name,
            overview=overview,
            detail=ctx.content,
            tags=ctx.tags or [],
            meta=meta,
        )

    async def _ensure_loaded(self):
        """Ensure ContextStore is loaded."""
        if not self._loaded:
            await self.load()

    # ──── ContextStore API Proxy (with auto-sync) ────

    async def set(
        self,
        path: str,
        glance: str,
        overview: Any = None,
        detail: Any = None,
        tags: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        **kwargs,
    ):
        """Set a context entry (syncs to DB).

        Args:
            path: Context path
            glance: One-line summary
            overview: Structured summary
            detail: Full content
            tags: Tags
            meta: Metadata
            **kwargs: Additional WorkspaceContext fields
        """
        await self._ensure_loaded()

        # Update ContextStore
        self._store.set(
            path=path,
            glance=glance,
            overview=overview,
            detail=detail,
            tags=tags,
            meta=meta,
        )

        # Sync to DB
        await self._sync_to_db(path, glance, overview, detail, tags, meta, **kwargs)

    async def _sync_to_db(
        self,
        path: str,
        glance: str,
        overview: Any,
        detail: Any,
        tags: list[str] | None,
        meta: dict[str, Any] | None,
        **kwargs,
    ):
        """Sync ContextStore entry to WorkspaceContext table."""
        import json

        normalized_path = "/" + path.lstrip("/")

        # Find existing or create new
        stmt = select(WorkspaceContext).where(
            WorkspaceContext.workspace_id == self.workspace_id,
            WorkspaceContext.path == normalized_path,
        )
        result = await self.session.execute(stmt)
        ctx = result.scalar_one_or_none()

        # Prepare overview as string
        summary_str = overview
        if isinstance(overview, dict):
            summary_str = json.dumps(overview, ensure_ascii=False)

        # Prepare detail as string
        content_str = detail
        if isinstance(detail, (dict, list)):
            content_str = json.dumps(detail, ensure_ascii=False)
        elif detail is not None:
            content_str = str(detail)

        if ctx:
            # Update existing
            ctx.glance = glance
            ctx.summary = summary_str
            ctx.content = content_str
            ctx.tags = tags
            ctx.meta = meta or {}

            # Update additional fields
            for k, v in kwargs.items():
                if hasattr(ctx, k):
                    setattr(ctx, k, v)
        else:
            # Create new
            # Extract name from kwargs or use glance
            name = kwargs.pop("name", glance)

            ctx = WorkspaceContext(
                workspace_id=self.workspace_id,
                path=normalized_path,
                name=name,
                glance=glance,
                summary=summary_str,
                content=content_str,
                tags=tags,
                meta=meta or {},
                **{k: v for k, v in kwargs.items() if hasattr(WorkspaceContext, k)}
            )
            self.session.add(ctx)

        await self.session.commit()

    async def delete(self, path: str, recursive: bool = False):
        """Delete context entry (syncs to DB).

        Args:
            path: Context path
            recursive: Delete descendants too
        """
        await self._ensure_loaded()

        # Delete from ContextStore
        self._store.delete(path, recursive=recursive)

        # Sync to DB (soft delete)
        normalized_path = "/" + path.lstrip("/")

        if recursive:
            # Soft delete all descendants
            stmt = select(WorkspaceContext).where(
                WorkspaceContext.workspace_id == self.workspace_id,
                WorkspaceContext.path.like(f"{normalized_path}%"),
            )
        else:
            # Soft delete single entry
            stmt = select(WorkspaceContext).where(
                WorkspaceContext.workspace_id == self.workspace_id,
                WorkspaceContext.path == normalized_path,
            )

        result = await self.session.execute(stmt)
        contexts = result.scalars().all()

        for ctx in contexts:
            ctx.is_deleted = True

        await self.session.commit()

    # ──── ContextStore Query API (read-only, no sync needed) ────

    async def get(self, path: str, level: str = "overview"):
        """Get context entry.

        Args:
            path: Context path
            level: Detail level (glance/overview/detail)

        Returns:
            Context data dict
        """
        await self._ensure_loaded()
        from aiwen.frameworks.context_layer import DetailLevel

        level_enum = DetailLevel.from_str(level)
        return self._store.get(path, level_enum)

    async def glob(self, pattern: str):  # -> QueryResult
        """Glob query.

        Args:
            pattern: Glob pattern (*, **)

        Returns:
            QueryResult
        """
        await self._ensure_loaded()
        return self._store.glob(pattern)

    async def children(self, prefix: str):  # -> QueryResult
        """Get direct children.

        Args:
            prefix: Path prefix

        Returns:
            QueryResult
        """
        await self._ensure_loaded()
        return self._store.children(prefix)

    async def descendants(self, prefix: str):  # -> QueryResult
        """Get all descendants.

        Args:
            prefix: Path prefix

        Returns:
            QueryResult
        """
        await self._ensure_loaded()
        return self._store.descendants(prefix)

    async def tree(self, root: str | None = None, level: str = "overview"):
        """Get tree structure.

        Args:
            root: Root path (None for full tree)
            level: Detail level

        Returns:
            Tree dict
        """
        await self._ensure_loaded()
        from aiwen.frameworks.context_layer import DetailLevel

        level_enum = DetailLevel.from_str(level)
        return self._store.tree(root, level_enum)

    async def glance(self, prefix: str | None = None):
        """Quick scan.

        Args:
            prefix: Path prefix (None for all)

        Returns:
            List of glance strings
        """
        await self._ensure_loaded()
        return self._store.glance(prefix)

    # ──── Batch Operations ────

    async def copy_from_global_context(
        self,
        context_ids: list[str | UUID],
        created_by: UUID | None = None,
    ):
        """Copy contexts from global Context table to this workspace.

        Args:
            context_ids: List of Context IDs to copy
            created_by: User who created the copy
        """
        from aiwen.models.context.context import Context

        stmt = select(Context).where(Context.id.in_(context_ids))
        result = await self.session.execute(stmt)
        contexts = result.scalars().all()

        for ctx in contexts:
            await self.set(
                path=ctx.path or f"imported/{ctx.id}",
                glance=ctx.glance or ctx.summary or ctx.content[:100],
                overview=ctx.summary,
                detail=ctx.content,
                tags=ctx.tags or [],
                meta={
                    "source_context_id": str(ctx.id),
                    "source_type": ctx.context_type,
                    "importance": ctx.importance,
                },
                created_by=created_by,
                content_type="text/plain",
            )

    @property
    def store(self):
        """Get ContextStore instance (for advanced usage).

        Warning: Direct modifications won't sync to DB.
        Use service methods (set, delete) for auto-sync.
        """
        return self._store
