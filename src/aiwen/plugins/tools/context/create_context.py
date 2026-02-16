"""Create context tool - add new context to workspace."""

from typing import Any

from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class CreateContextTool(InnerTool):
    """Create a new context entry in the workspace with progressive disclosure layers."""

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
        path: str = Field(
            description="Context path (e.g., 'tools/new_tool', 'knowledge/guide')"
        )
        glance: str = Field(
            description="One-line summary for quick scanning (e.g., 'Web Search — ✅ Ready')"
        )
        overview: dict[str, Any] | str | None = Field(
            default=None,
            description="Structured summary or brief text (Layer 2)",
        )
        detail: Any = Field(
            description="Full content/data (can be dict, string, or any JSON-serializable data)"
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
        from aiwen.extensions.database import get_session
        from aiwen.services.workspace_context.workspace_context_service import (
            WorkspaceContextService,
        )

        try:
            async with get_session("aiwen") as db:
                # Initialize service
                service = WorkspaceContextService(db, input_data.workspace_id)
                await service.load()

                # Create context
                await service.set(
                    path=input_data.path,
                    glance=input_data.glance,
                    overview=input_data.overview,
                    detail=input_data.detail,
                    tags=input_data.tags or [],
                    meta=input_data.meta or {},
                    name=input_data.name or input_data.glance,
                    content_type=input_data.content_type,
                )

                # Verify creation
                created = await service.get(input_data.path, level="overview")

                return ToolOutputSchema(
                    success=True,
                    message=f"Created context at: {input_data.path}",
                    data={
                        "path": input_data.path,
                        "glance": input_data.glance,
                        "tags": input_data.tags,
                        "context": created,
                    },
                )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to create context: {e!s}",
                error=str(e),
                data={"path": input_data.path},
            )
