"""Update context tool - modify SQL-backed context."""

from typing import Any

from pydantic import Field

from structure.core.enums import EventType
from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)
from structure.extensions.database import get_session
from structure.plugins.tools.context._sql_context import (
    find_context_by_path,
    normalize_path,
    publish_context_event,
    to_text,
)


class UpdateContextTool(InnerTool):
    """Update an existing context entry."""

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
        run_id: str | None = Field(default=None, description="Run ID")
        user_id: str | None = Field(default=None, description="User ID")
        path: str = Field(description="Context path to update")
        glance: str | None = Field(
            default=None, description="New glance text (one-line summary)"
        )
        overview: dict[str, Any] | str | None = Field(
            default=None, description="New overview content"
        )
        detail: Any | None = Field(default=None, description="New detail content")
        tags: list[str] | None = Field(
            default=None, description="New tags (replaces existing tags)"
        )
        meta: dict[str, Any] | None = Field(
            default=None, description="New metadata (merges with existing)"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID

        try:
            workspace_id = UUID(input_data.workspace_id)
            normalized_path = normalize_path(input_data.path)
            updated_fields: list[str] = []

            async with get_session("structure") as session:
                ctx = await find_context_by_path(
                    session,
                    workspace_id,
                    normalized_path,
                    user_id=input_data.user_id,
                )
                if ctx is None:
                    return ToolOutputSchema(
                        success=False,
                        message=f"Context not found at path: {normalized_path}",
                        data={"path": normalized_path, "exists": False},
                    )

                if input_data.glance is not None:
                    ctx.glance = input_data.glance
                    updated_fields.append("glance")
                if input_data.overview is not None:
                    ctx.meta = {
                        **(ctx.meta or {}),
                        "overview": to_text(input_data.overview),
                    }
                    updated_fields.append("overview")
                if input_data.detail is not None:
                    new_content = to_text(input_data.detail)
                    if new_content != ctx.content:
                        ctx.content = new_content
                        ctx.embedding_384 = None
                        ctx.embedding_768 = None
                        ctx.embedding_1024 = None
                        ctx.embedding_1536 = None
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
                        data={"path": normalized_path},
                    )

                await publish_context_event(
                    session,
                    EventType.CONTEXT_UPDATED,
                    workspace_id,
                    run_id=input_data.run_id,
                    user_id=input_data.user_id,
                    payload={
                        "path": normalized_path,
                        "context_id": str(ctx.id),
                        "updated_fields": updated_fields,
                    },
                )
                await session.commit()

            return ToolOutputSchema(
                success=True,
                message=f"Updated context at: {normalized_path}",
                data={"path": normalized_path, "updated_fields": updated_fields},
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to update context: {e!s}",
                error=str(e),
                data={"path": input_data.path},
            )
