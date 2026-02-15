"""Update context tool - modify existing context."""

from typing import Any

from pydantic import Field

from aiwen.interfaces.tool import (
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
        path: str = Field(
            description="Context path to update"
        )
        glance: str | None = Field(
            default=None,
            description="New glance text (one-line summary)",
        )
        overview: dict[str, Any] | str | None = Field(
            default=None,
            description="New overview content",
        )
        detail: Any | None = Field(
            default=None,
            description="New detail content",
        )
        tags: list[str] | None = Field(
            default=None,
            description="New tags (replaces existing tags)",
        )
        meta: dict[str, Any] | None = Field(
            default=None,
            description="New metadata (merges with existing)",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from aiwen.extensions.database import get_session
        from aiwen.services.workspace_context_service import WorkspaceContextService

        try:
            async with get_session("aiwen") as db:
                # Initialize service
                service = WorkspaceContextService(db, input_data.workspace_id)
                await service.load()

                # Check if context exists
                existing = await service.get(input_data.path, level="overview")
                if existing is None:
                    return ToolOutputSchema(
                        success=False,
                        message=f"Context not found at path: {input_data.path}",
                        data={"path": input_data.path, "exists": False},
                    )

                # Prepare update - only include provided fields
                update_data = {}
                if input_data.glance is not None:
                    update_data["glance"] = input_data.glance
                if input_data.overview is not None:
                    update_data["overview"] = input_data.overview
                if input_data.detail is not None:
                    update_data["detail"] = input_data.detail
                if input_data.tags is not None:
                    update_data["tags"] = input_data.tags
                if input_data.meta is not None:
                    # Merge with existing meta
                    existing_meta = existing.get("meta", {})
                    update_data["meta"] = {**existing_meta, **input_data.meta}

                if not update_data:
                    return ToolOutputSchema(
                        success=False,
                        message="No fields provided to update",
                        data={"path": input_data.path},
                    )

                # Update context (re-set with merged data)
                await service.set(
                    path=input_data.path,
                    glance=update_data.get("glance", existing.get("glance", "")),
                    overview=update_data.get("overview", existing.get("overview")),
                    detail=update_data.get("detail", existing.get("content")),
                    tags=update_data.get("tags", existing.get("tags", [])),
                    meta=update_data.get("meta", existing.get("meta", {})),
                )

                # Get updated context
                updated = await service.get(input_data.path, level="overview")

                return ToolOutputSchema(
                    success=True,
                    message=f"Updated context at: {input_data.path}",
                    data={
                        "path": input_data.path,
                        "updated_fields": list(update_data.keys()),
                        "context": updated,
                    },
                )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to update context: {str(e)}",
                error=str(e),
                data={"path": input_data.path},
            )
