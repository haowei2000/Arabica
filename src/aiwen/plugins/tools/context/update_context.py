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
        from sqlalchemy import select

        from aiwen.extensions.database import get_session
        from aiwen.models.context.workspace_context import WorkspaceContext

        try:
            normalized_path = "/" + input_data.path.lstrip("/")
            async with get_session("aiwen") as db:
                stmt = select(WorkspaceContext).where(
                    WorkspaceContext.workspace_id == input_data.workspace_id,
                    WorkspaceContext.path == normalized_path,
                    WorkspaceContext.is_deleted == False,  # noqa: E712
                )
                result = await db.execute(stmt)
                ctx = result.scalar_one_or_none()

                if ctx is None:
                    return ToolOutputSchema(
                        success=False,
                        message=f"Context not found at path: {input_data.path}",
                        data={"path": input_data.path, "exists": False},
                    )

                updated_fields = []

                if input_data.glance is not None:
                    ctx.glance = input_data.glance
                    updated_fields.append("glance")

                if input_data.overview is not None:
                    ctx.summary = (
                        json.dumps(input_data.overview, ensure_ascii=False)
                        if isinstance(input_data.overview, dict)
                        else input_data.overview
                    )
                    updated_fields.append("overview")

                if input_data.detail is not None:
                    ctx.content = (
                        json.dumps(input_data.detail, ensure_ascii=False)
                        if isinstance(input_data.detail, (dict, list))
                        else str(input_data.detail)
                    )
                    updated_fields.append("detail")

                if input_data.tags is not None:
                    ctx.tags = input_data.tags
                    updated_fields.append("tags")

                if input_data.meta is not None:
                    ctx.meta = {**(ctx.meta or {}), **input_data.meta}
                    updated_fields.append("meta")

                if not updated_fields:
                    return ToolOutputSchema(
                        success=False,
                        message="No fields provided to update",
                        data={"path": input_data.path},
                    )

                await db.commit()

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
