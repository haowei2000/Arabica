"""Workspace context read model.

This is the single read entrypoint for tools that need structured context. It
keeps the existing WorkspaceContextService merge semantics while hiding the
underlying Context + WorkspaceContext dual-table layout from callers.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from structure.services.workspace_context.workspace_context_service import (
    WorkspaceContextService,
)


class WorkspaceContextReadModel(WorkspaceContextService):
    """Version-cacheable read facade over workspace-visible context rows."""

    def __init__(
        self,
        session: AsyncSession,
        workspace_id: str | UUID,
        owner_id: str | UUID | None = None,
        *,
        context_version: int | None = None,
    ) -> None:
        super().__init__(session, workspace_id, owner_id)
        self.context_version = context_version
