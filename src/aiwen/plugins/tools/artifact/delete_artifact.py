"""Delete artifact tool - remove an artifact from the workspace."""

from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class DeleteArtifactTool(InnerTool):
    """Delete an artifact by its ID."""

    METADATA = ToolMetadata(
        name="delete_artifact",
        display_name="Delete Artifact",
        description="Permanently delete an artifact by its ID.",
        category="artifact",
        tags=["artifact", "delete", "remove"],
        timeout=10,
    )

    class InputSchema(ToolInputSchema):
        artifact_id: str = Field(description="Artifact ID to delete")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID

        from sqlalchemy import delete

        from aiwen.extensions.database import get_session
        from aiwen.models.runs.artifact import Artifact

        try:
            async with get_session("aiwen") as db:
                stmt = delete(Artifact).where(Artifact.id == UUID(input_data.artifact_id))
                result = await db.execute(stmt)
                await db.commit()
                deleted = result.rowcount

            if deleted == 0:
                return ToolOutputSchema(
                    success=False,
                    message=f"Artifact not found: {input_data.artifact_id}",
                    data={"artifact_id": input_data.artifact_id, "exists": False},
                )

            return ToolOutputSchema(
                success=True,
                message=f"Deleted artifact: {input_data.artifact_id}",
                data={"artifact_id": input_data.artifact_id},
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to delete artifact: {e!s}",
                error=str(e),
                data={"artifact_id": input_data.artifact_id},
            )
