"""Adapter for integrating Context model with ContextLayer framework."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.context.context import Context


class ContextStoreAdapter:
    """Adapter to load Context models into ContextStore framework.

    Usage:
        # Load workspace contexts
        adapter = ContextStoreAdapter(session)
        store = await adapter.load_workspace_store(workspace_id)

        # Use ContextStore features
        tools = store.children("tools")
        a result = store.glob("tools/**")
        data = store.get("tools/web_search", DetailLevel.OVERVIEW)
    """

    def __init__(self, session: AsyncSession):
        """Initialize the adapter with a database session.

        Args:
            session: SQLAlchemy async session
        """
        self.session = session

    async def load_workspace_store(
        self,
        workspace_id: str | UUID,
        title: str | None = None,
    ):  # -> ContextStore:
        """Load all relevant contexts for a workspace into ContextStore.

        Args:
            workspace_id: Workspace ID
            title: Store title (defaults to "Workspace {workspace_id}")

        Returns:
            ContextStore instance loaded with workspace contexts
        """
        from aiwen.frameworks.context import ContextStore

        workspace_id = str(workspace_id)
        store = ContextStore(
            title=title or f"Workspace {workspace_id}",
            description=f"Context store for workspace {workspace_id}"
        )

        # Build workspace path prefix
        workspace_prefix = f"/{workspace_id}"

        # Query all contexts under this workspace
        stmt = select(Context).where(
            Context.path.like(f"{workspace_prefix}/%")
        ).order_by(Context.path)

        result = await self.session.execute(stmt)
        contexts = result.scalars().all()

        # Register schema nodes (骨架节点) for standard paths
        await self._register_schemas(store, workspace_id)

        # Load all context entries
        for ctx in contexts:
            await self._load_context_entry(store, ctx)

        return store

    async def _register_schemas(self, store, workspace_id: str):
        """Register schema nodes for the standard workspace structure.

        Args:
            store: ContextStore instance
            workspace_id: Workspace ID
        """
        from aiwen.frameworks.context import count_aggregator

        # Register a standard structure with aggregators
        base_path = workspace_id

        # Root workspace node
        store.schema(
            base_path,
            glance=f"Workspace {workspace_id}",
            aggregator=count_aggregator,
            tags=["workspace", "root"]
        )

        # Standard sub-paths
        paths = {
            ContextPathSuffix.TOOLS: "Available Tools",
            ContextPathSuffix.SKILLS: "Available Skills",
            ContextPathSuffix.KNOWLEDGE: "Knowledge Base",
            ContextPathSuffix.LONG_MEMORY: "Long Memory",
            ContextPathSuffix.SHORT_MEMORY: "Short Memory",
        }

        for suffix, title in paths.items():
            path = build_context_path(workspace_id, suffix)
            store.schema(
                path.lstrip("/"),  # Remove leading slash for ContextStore
                glance=title,
                aggregator=count_aggregator,
                tags=["workspace", suffix]
            )

    async def _load_context_entry(self, store, ctx: Context):
        """Load a single Context model into ContextStore.

        Args:
            store: ContextStore instance
            ctx: Context model instance
        """
        # Normalize path (remove leading slash)
        path = ctx.path.lstrip("/") if ctx.path else str(ctx.id)

        # Build meta from model fields
        meta = ctx.meta or {}
        meta.update({
            "id": str(ctx.id),
            "user_id": str(ctx.user_id),
            "context_type": ctx.context_type,
            "importance": ctx.importance,
            "created_at": ctx.created_at.isoformat() if ctx.created_at else None,
            "updated_at": ctx.updated_at.isoformat() if ctx.updated_at else None,
        })

        if ctx.source_id:
            meta["source_id"] = str(ctx.source_id)
        if ctx.s3_key:
            meta["s3_key"] = ctx.s3_key

        # Set entry in store
        store.set(
            path=path,
            glance=ctx.glance or ctx.content[:100],
            overview=None,
            detail=ctx.content,
            tags=ctx.tags or [],
            meta=meta,
        )

    async def context_to_entry(self, ctx: Context):  # -> ContextEntry:
        """Convert a Context model to ContextEntry.

        Args:
            ctx: Context model instance

        Returns:
            ContextEntry instance
        """
        from aiwen.frameworks.context import ContextEntry

        # Build meta
        meta = ctx.meta or {}
        meta.update({
            "id": str(ctx.id),
            "user_id": str(ctx.user_id),
            "context_type": ctx.context_type,
            "importance": ctx.importance,
        })

        return ContextEntry(
            glance=ctx.glance or ctx.content[:100],
            overview=None,
            detail=ctx.content,
            tags=ctx.tags or [],
            meta=meta,
        )

    async def load_by_paths(
        self,
        paths: list[str],
        title: str = "Context Store"
    ):  # -> ContextStore:
        """Load specific contexts by paths.

        Args:
            paths: List of context paths
            title: Store title

        Returns:
            ContextStore with loaded contexts
        """
        from aiwen.frameworks.context import ContextStore

        store = ContextStore(title=title)

        stmt = select(Context).where(Context.path.in_(paths))
        result = await self.session.execute(stmt)
        contexts = result.scalars().all()

        for ctx in contexts:
            await self._load_context_entry(store, ctx)

        return store

    async def load_by_tags(
        self,
        tags: list[str],
        title: str = "Context Store"
    ):  # -> ContextStore:
        """Load contexts by tags.

        Args:
            tags: List of tags to filter
            title: Store title

        Returns:
            ContextStore with matching contexts
        """
        from aiwen.frameworks.context import ContextStore

        store = ContextStore(title=title)

        # Query contexts with any of the tags
        stmt = select(Context).where(
            Context.tags.op("&&")(tags)
        )
        result = await self.session.execute(stmt)
        contexts = result.scalars().all()

        for ctx in contexts:
            await self._load_context_entry(store, ctx)

        return store


# ──── 便捷函数 ────

async def load_workspace_contexts(
    session: AsyncSession,
    workspace_id: str | UUID,
):  # -> ContextStore:
    """Convenience function to load workspace contexts.

    Args:
        session: Database session
        workspace_id: Workspace ID

    Returns:
        ContextStore instance
    """
    adapter = ContextStoreAdapter(session)
    return await adapter.load_workspace_store(workspace_id)


async def query_and_display(
    session: AsyncSession,
    workspace_id: str | UUID,
    pattern: str = "**",
):
    """Query and display workspace contexts.

    Args:
        session: Database session
        workspace_id: Workspace ID
        pattern: Glob pattern (default: all)

    Example:
        # Show all tools
        await query_and_display(session, "ws_123", "tools/**")

        # Show workspace overview
        await query_and_display(session, "ws_123")
    """
    from aiwen.frameworks.context import DetailLevel

    store = await load_workspace_contexts(session, workspace_id)

    # Query with pattern
    if pattern == "**":
        # Show full tree
        print(store.render_text(DetailLevel.OVERVIEW))
    else:
        # Show matching nodes
        results = store.glob(pattern)
        print(f"\n🔍 Query: {pattern}")
        print(f"📊 Results: {len(results)} items\n")
        for path, entry in results:
            print(f"  {path} → {entry.glance}")
