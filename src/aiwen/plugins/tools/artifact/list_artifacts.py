"""List artifacts tool - list artifacts for a workspace or run."""

from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class ListArtifactsTool(InnerTool):
    """List artifacts in a workspace, optionally filtered by run or type."""

    METADATA = ToolMetadata(
        name="list_artifacts",
        display_name="List Artifacts",
        description=(
            "List artifacts in a workspace, optionally filtered by run ID or artifact type. "
            "Returns artifact IDs, names, types, and versions (not content)."
        ),
        category="artifact",
        tags=["artifact", "list", "search"],
        timeout=10,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        run_id: str | None = Field(
            default=None, description="Filter by run ID (optional)"
        )
        artifact_type: str | None = Field(
            default=None,
            description="Filter by type: text, code, file, image, document, data, other",
        )
        limit: int = Field(default=20, description="Maximum number of results (1-100)", ge=1, le=100)

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID

        from sqlalchemy import select

        from aiwen.extensions.database import get_session
        from aiwen.models.runs.artifact import Artifact

        try:
            async with get_session("aiwen") as db:
                stmt = (
                    select(
                        Artifact.id,
                        Artifact.name,
                        Artifact.artifact_type,
                        Artifact.content_type,
                        Artifact.version,
                        Artifact.run_id,
                        Artifact.created_at,
                    )
                    .where(Artifact.workspace_id == UUID(input_data.workspace_id))
                    .order_by(Artifact.created_at.desc())
                    .limit(input_data.limit)
                )

                if input_data.run_id:
                    stmt = stmt.where(Artifact.run_id == UUID(input_data.run_id))

                if input_data.artifact_type:
                    stmt = stmt.where(Artifact.artifact_type == input_data.artifact_type)

                result = await db.execute(stmt)
                rows = result.all()

            artifacts = [
                {
                    "artifact_id": str(row.id),
                    "name": row.name,
                    "artifact_type": row.artifact_type,
                    "content_type": row.content_type,
                    "version": row.version,
                    "run_id": str(row.run_id) if row.run_id else None,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]

            return ToolOutputSchema(
                success=True,
                message=f"Found {len(artifacts)} artifact(s)",
                data={"artifacts": artifacts, "count": len(artifacts)},
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to list artifacts: {e!s}",
                error=str(e),
            )
