"""CRUD operations for Artifact model."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.models.runs.artifact import Artifact


class ArtifactCRUD:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_by_workspace(
        self,
        workspace_id: str | UUID,
        run_id: str | UUID | None = None,
        artifact_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, list[Artifact]]:
        """List artifacts in a workspace with optional filters."""
        stmt = select(Artifact).where(Artifact.workspace_id == str(workspace_id))
        if run_id is not None:
            stmt = stmt.where(Artifact.run_id == str(run_id))
        if artifact_type is not None:
            stmt = stmt.where(Artifact.artifact_type == artifact_type)

        count_stmt = select(Artifact).where(Artifact.workspace_id == str(workspace_id))
        if run_id is not None:
            count_stmt = count_stmt.where(Artifact.run_id == str(run_id))
        if artifact_type is not None:
            count_stmt = count_stmt.where(Artifact.artifact_type == artifact_type)

        total_result = await self.db.execute(count_stmt)
        total = len(total_result.scalars().all())

        stmt = stmt.order_by(Artifact.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return total, list(result.scalars().all())

    async def get_by_id(self, artifact_id: str | UUID) -> Artifact | None:
        """Get a single artifact by ID."""
        result = await self.db.execute(
            select(Artifact).where(Artifact.id == str(artifact_id))
        )
        return result.scalar_one_or_none()
