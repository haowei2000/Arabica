"""Create artifact tool - store a new agent-produced output."""

import asyncio
from typing import Any

from pydantic import Field

from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class CreateArtifactTool(InnerTool):
    """Create a new artifact to store an agent-produced output."""

    METADATA = ToolMetadata(
        name="create_artifact",
        display_name="Create Artifact",
        description=(
            "Create a new artifact to store agent-produced outputs such as text, code, "
            "documents, or data. Content is automatically uploaded to S3 storage. "
            "Returns the artifact ID and S3 URL for future reference."
        ),
        category="artifact",
        tags=["artifact", "create", "output", "store"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        name: str = Field(
            description="Artifact name (e.g., 'analysis_report', 'solution.py')"
        )
        content: str | None = Field(
            default=None, description="Artifact content to store"
        )
        artifact_type: str = Field(
            default="text",
            description="Type: text, code, file, image, document, data, other",
        )
        content_type: str | None = Field(
            default="text/plain",
            description="MIME type (e.g., 'text/plain', 'application/json', 'text/x-python')",
        )
        run_id: str | None = Field(
            default=None, description="Run ID that produced this artifact"
        )
        meta: dict[str, Any] | None = Field(
            default=None, description="Additional metadata"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID, uuid4

        from structure.config.factory import get_settings
        from structure.extensions.database import get_session
        from structure.extensions.storage.global_storage import get_global_s3_storage
        from structure.models.runs.artifact import Artifact

        try:
            artifact_id = uuid4()
            s3_key: str | None = None
            s3_url: str | None = None

            if input_data.content:
                storage = get_global_s3_storage()
                settings = get_settings()
                s3_key = f"artifacts/{input_data.workspace_id}/{artifact_id}"
                await asyncio.to_thread(
                    storage.put_bytes,
                    s3_key,
                    input_data.content.encode("utf-8"),
                    content_type=input_data.content_type or "text/plain",
                )
                s3_url = f"{settings.rustfs.endpoint}/{storage.bucket}/{s3_key}"

            artifact = Artifact(
                id=artifact_id,
                workspace_id=UUID(input_data.workspace_id),
                run_id=UUID(input_data.run_id) if input_data.run_id else None,
                name=input_data.name,
                artifact_type=input_data.artifact_type,
                content_type=input_data.content_type,
                content=input_data.content,
                s3_key=s3_key,
                s3_url=s3_url,
                version=1,
                meta=input_data.meta or {},
            )

            async with get_session("structure") as db:
                db.add(artifact)
                await db.commit()

            return ToolOutputSchema(
                success=True,
                message=f"Created artifact '{input_data.name}'",
                data={
                    "artifact_id": str(artifact_id),
                    "name": input_data.name,
                    "artifact_type": input_data.artifact_type,
                    "s3_key": s3_key,
                    "s3_url": s3_url,
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to create artifact: {e!s}",
                error=str(e),
            )
