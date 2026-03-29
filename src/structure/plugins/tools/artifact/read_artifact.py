"""Read artifact tool - retrieve an artifact by ID."""

from pydantic import Field

from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class ReadArtifactTool(InnerTool):
    """Read an artifact by its ID."""

    METADATA = ToolMetadata(
        name="read_artifact",
        display_name="Read Artifact",
        description="Retrieve an artifact's content and metadata by its ID.",
        category="artifact",
        tags=["artifact", "read", "get"],
        timeout=10,
    )

    class InputSchema(ToolInputSchema):
        artifact_id: str = Field(description="Artifact ID to retrieve")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID

        from sqlalchemy import select

        from structure.extensions.database import get_session
        from structure.models.runs.artifact import Artifact

        try:
            async with get_session("structure") as db:
                stmt = select(Artifact).where(Artifact.id == UUID(input_data.artifact_id))
                result = await db.execute(stmt)
                artifact = result.scalar_one_or_none()

            if artifact is None:
                return ToolOutputSchema(
                    success=False,
                    message=f"Artifact not found: {input_data.artifact_id}",
                    data={"artifact_id": input_data.artifact_id, "exists": False},
                )

            return ToolOutputSchema(
                success=True,
                message=f"Retrieved artifact '{artifact.name}'",
                data={
                    "artifact_id": str(artifact.id),
                    "name": artifact.name,
                    "artifact_type": artifact.artifact_type,
                    "content_type": artifact.content_type,
                    "content": artifact.content,
                    "s3_key": artifact.s3_key,
                    "s3_url": artifact.s3_url,
                    "version": artifact.version,
                    "meta": artifact.meta,
                    "run_id": str(artifact.run_id) if artifact.run_id else None,
                    "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
                    "updated_at": artifact.updated_at.isoformat() if artifact.updated_at else None,
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to read artifact: {e!s}",
                error=str(e),
                data={"artifact_id": input_data.artifact_id},
            )
