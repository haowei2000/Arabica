"""Adapter for WorkspaceContextService to provide a unified context interface."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.services.workspace_context.workspace_context_service import WorkspaceContextService


async def load_workspace_contexts(
    session: AsyncSession,
    workspace_id: str | UUID,
) -> WorkspaceContextService:
    """Convenience function to load and initialize a WorkspaceContextService.

    Args:
        session: Database session
        workspace_id: Workspace ID

    Returns:
        Loaded WorkspaceContextService instance
    """
    service = WorkspaceContextService(session, workspace_id)
    await service.load()
    return service


async def query_and_display(
    session: AsyncSession,
    workspace_id: str | UUID,
    pattern: str = "*",
):
    """Query and display workspace contexts using glob patterns.

    Args:
        session: Database session
        workspace_id: Workspace ID
        pattern: Glob pattern (default: all top-level)
    """
    service = await load_workspace_contexts(session, workspace_id)
    results = await service.glob(pattern)
    
    print(f"\n🔍 Query: {pattern}")
    print(f"📊 Results: {len(results)} items\n")
    for item in results:
        print(f"  {item['path']} → {item['glance']}")
