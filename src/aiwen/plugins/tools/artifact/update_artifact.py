"""Update artifact tool - modify an existing artifact's content or metadata."""

import asyncio
from typing import Any

from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class UpdateArtifactTool(InnerTool):
    """Update an existing artifact's content, name, or metadata."""

    METADATA = ToolMetadata(
        name="update_artifact",
        display_name="Update Artifact",
        description=(
            "Update an artifact's content, name, or metadata. When content changes, "
            "it is re-uploaded to S3 automatically and the version number increments."
        ),
        category="artifact",
        tags=["artifact", "update", "modify", "edit"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        artifact_id: str = Field(description="Artifact ID to update")
        name: str | None = Field(default=None, description="New artifact name")
        content: str | None = Field(default=None, description="New artifact content")
        artifact_type: str | None = Field(default=None, description="New artifact type")
        content_type: str | None = Field(default=None, description="New MIME type")
        meta: dict[str, Any] | None = Field(
            default=None, description="Metadata to merge into existing metadata"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID

        from sqlalchemy import select

        from aiwen.config.factory import get_settings
        from aiwen.extensions.database import get_session
        from aiwen.extensions.storage.global_storage import get_global_s3_storage
        from aiwen.models.runs.artifact import Artifact

        try:
            async with get_session("aiwen") as db:
                stmt = select(Artifact).where(Artifact.id == UUID(input_data.artifact_id))
                result = await db.execute(stmt)
                artifact = result.scalar_one_or_none()

                if artifact is None:
                    return ToolOutputSchema(
                        success=False,
                        message=f"Artifact not found: {input_data.artifact_id}",
                        data={"artifact_id": input_data.artifact_id, "exists": False},
                    )

                updated_fields = []

                if input_data.name is not None:
                    artifact.name = input_data.name
                    updated_fields.append("name")

                if input_data.artifact_type is not None:
                    artifact.artifact_type = input_data.artifact_type
                    updated_fields.append("artifact_type")

                if input_data.content_type is not None:
                    artifact.content_type = input_data.content_type
                    updated_fields.append("content_type")

                if input_data.content is not None:
                    # Re-upload to the existing S3 key (or generate one if missing)
                    storage = get_global_s3_storage()
                    settings = get_settings()
                    s3_key = artifact.s3_key or f"artifacts/{artifact.workspace_id}/{artifact.id}"
                    await asyncio.to_thread(
                        storage.put_bytes,
                        s3_key,
                        input_data.content.encode("utf-8"),
                        content_type=artifact.content_type or "text/plain",
                    )
                    artifact.content = input_data.content
                    artifact.s3_key = s3_key
                    artifact.s3_url = f"{settings.rustfs.endpoint}/{storage.bucket}/{s3_key}"
                    artifact.version += 1
                    updated_fields.extend(["content", "s3_key", "s3_url"])

                if input_data.meta is not None:
                    artifact.meta = {**(artifact.meta or {}), **input_data.meta}
                    updated_fields.append("meta")

                if not updated_fields:
                    return ToolOutputSchema(
                        success=False,
                        message="No fields provided to update",
                        data={"artifact_id": input_data.artifact_id},
                    )

                await db.commit()

            return ToolOutputSchema(
                success=True,
                message=f"Updated artifact '{artifact.name}' (v{artifact.version})",
                data={
                    "artifact_id": input_data.artifact_id,
                    "updated_fields": updated_fields,
                    "version": artifact.version,
                    "s3_url": artifact.s3_url,
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to update artifact: {e!s}",
                error=str(e),
                data={"artifact_id": input_data.artifact_id},
            )
