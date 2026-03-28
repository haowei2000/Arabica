"""Update context tool - modify existing context."""

import json
from typing import Any

from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class UpdateContextTool(InnerTool):
    """Update an existing context entry - modify any field (glance/overview/detail/tags)."""

    METADATA = ToolMetadata(
        name="update_context",
        display_name="Update Context",
        description="Update existing context fields (glance, overview, detail, tags, etc.)",
        category="context",
        tags=["context", "update", "modify", "edit"],
        timeout=15,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        path: str = Field(description="Context path to update")
        glance: str | None = Field(default=None, description="New glance text (one-line summary)")
        overview: dict[str, Any] | str | None = Field(default=None, description="New overview content")
        detail: Any | None = Field(default=None, description="New detail content")
        tags: list[str] | None = Field(default=None, description="New tags (replaces existing tags)")
        meta: dict[str, Any] | None = Field(default=None, description="New metadata (merges with existing)")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID
        from aiwen.services.context.client import context_service_client

        try:
            workspace_id = UUID(input_data.workspace_id)
            normalized_path = "/" + input_data.path.lstrip("/")

            # Fetch existing context to perform partial update
            ctx = await context_service_client.get_context(workspace_id, normalized_path)

            if ctx is None:
                return ToolOutputSchema(
                    success=False,
                    message=f"Context not found at path: {input_data.path}",
                    data={"path": input_data.path, "exists": False},
                )

            updated_fields = []
            update_payload = {
                "path": normalized_path,
                "content": ctx.get("content", ""),
            }

            if input_data.glance is not None:
                update_payload["glance"] = input_data.glance
                updated_fields.append("glance")

            if input_data.overview is not None:
                update_payload["summary"] = (
                    json.dumps(input_data.overview, ensure_ascii=False)
                    if isinstance(input_data.overview, dict)
                    else input_data.overview
                )
                updated_fields.append("overview")

            if input_data.detail is not None:
                update_payload["content"] = (
                    json.dumps(input_data.detail, ensure_ascii=False)
                    if isinstance(input_data.detail, (dict, list))
                    else str(input_data.detail)
                )
                updated_fields.append("detail")

            if input_data.tags is not None:
                update_payload["tags"] = input_data.tags
                updated_fields.append("tags")

            if input_data.meta is not None:
                existing_meta = ctx.get("meta") or {}
                update_payload["meta"] = {**existing_meta, **input_data.meta}
                updated_fields.append("meta")

            if not updated_fields:
                return ToolOutputSchema(
                    success=False,
                    message="No fields provided to update",
                    data={"path": input_data.path},
                )

            # Perform the update via create_context (upsert)
            await context_service_client.create_context(
                workspace_id=workspace_id,
                **update_payload
            )

            return ToolOutputSchema(
                success=True,
                message=f"Updated context at: {input_data.path}",
                data={
                    "path": input_data.path,
                    "updated_fields": updated_fields,
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to update context: {e!s}",
                error=str(e),
                data={"path": input_data.path},
            )
