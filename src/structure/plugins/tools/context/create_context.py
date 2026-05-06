"""Create context tool - add new SQL-backed context to a workspace."""

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
    normalize_path,
    publish_context_event,
    to_text,
    upsert_workspace_context,
)


class CreateContextTool(InnerTool):
    """Create a new context entry with progressive disclosure layers."""

    METADATA = ToolMetadata(
        name="create_context",
        display_name="Create Context",
        description="Create a new context entry with glance/overview/detail layers",
        category="context",
        tags=["context", "create", "add", "write"],
        timeout=15,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        run_id: str | None = Field(default=None, description="Run ID")
        user_id: str | None = Field(default=None, description="User ID")
        path: str = Field(
            description="Context path (e.g., 'tools/new_tool', 'knowledge/guide')"
        )
        glance: str = Field(description="One-line summary for quick scanning")
        overview: dict[str, Any] | str | None = Field(
            default=None,
            description="Structured summary or brief text (Layer 2)",
        )
        detail: Any = Field(
            description="Full content/data (dict, string, or JSON-serializable data)"
        )
        tags: list[str] | None = Field(
            default=None,
            description="Tags for categorization and filtering",
        )
        meta: dict[str, Any] | None = Field(
            default=None,
            description="Additional metadata",
        )
        name: str | None = Field(
            default=None,
            description="Display name (defaults to glance if not provided)",
        )
        content_type: str | None = Field(
            default="text/plain",
            description="Content MIME type",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID

        try:
            workspace_id = UUID(input_data.workspace_id)
            normalized_path = normalize_path(input_data.path)
            overview = to_text(input_data.overview) if input_data.overview else None
            meta = {
                **(input_data.meta or {}),
                "name": input_data.name or input_data.glance,
                "overview": overview,
                "content_type": input_data.content_type,
            }

            async with get_session("structure") as session:
                ctx, created = await upsert_workspace_context(
                    session,
                    workspace_id,
                    path=normalized_path,
                    glance=input_data.glance,
                    content=to_text(input_data.detail),
                    user_id=input_data.user_id,
                    tags=input_data.tags or [],
                    meta=meta,
                )
                await publish_context_event(
                    session,
                    EventType.CONTEXT_CREATED
                    if created
                    else EventType.CONTEXT_UPDATED,
                    workspace_id,
                    run_id=input_data.run_id,
                    user_id=input_data.user_id,
                    payload={
                        "path": normalized_path,
                        "context_id": str(ctx.id),
                        "glance": input_data.glance,
                        "tags": input_data.tags or [],
                    },
                )
                await session.commit()

            return ToolOutputSchema(
                success=True,
                message=f"Created context at: {normalized_path}",
                data={
                    "path": normalized_path,
                    "glance": input_data.glance,
                    "tags": input_data.tags or [],
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to create context: {e!s}",
                error=str(e),
                data={"path": input_data.path},
            )
