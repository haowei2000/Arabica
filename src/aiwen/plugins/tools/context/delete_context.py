"""Delete context tool - remove context from workspace."""

from pydantic import Field

from aiwen.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class DeleteContextTool(InnerTool):
    """Delete a context entry (soft delete, can be recursive)."""

    METADATA = ToolMetadata(
        name="delete_context",
        display_name="Delete Context",
        description="Delete context entry (soft delete). Can delete recursively with descendants.",
        category="context",
        tags=["context", "delete", "remove", "rm"],
        timeout=15,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        path: str = Field(
            description="Context path to delete"
        )
        recursive: bool = Field(
            default=False,
            description="If true, delete all descendants too (like rm -r)",
        )
        confirm: bool = Field(
            default=False,
            description="Must be true to actually delete (safety check)",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from aiwen.extensions.database import get_session
        from aiwen.services.workspace_context_service import WorkspaceContextService

        try:
            # Safety check
            if not input_data.confirm:
                return ToolOutputSchema(
                    success=False,
                    message="Delete not confirmed. Set confirm=true to proceed.",
                    data={
                        "path": input_data.path,
                        "recursive": input_data.recursive,
                        "confirmed": False,
                    },
                )

            async with get_session("aiwen") as db:
                # Initialize service
                service = WorkspaceContextService(db, input_data.workspace_id)
                await service.load()

                # Check if context exists
                existing = await service.get(input_data.path, level="glance")
                if existing is None:
                    return ToolOutputSchema(
                        success=False,
                        message=f"Context not found at path: {input_data.path}",
                        data={"path": input_data.path, "exists": False},
                    )

                # Get descendants count if recursive
                deleted_paths = [input_data.path]
                if input_data.recursive:
                    descendants = await service.descendants(input_data.path)
                    deleted_paths.extend(descendants.paths())

                # Delete
                await service.delete(input_data.path, recursive=input_data.recursive)

                return ToolOutputSchema(
                    success=True,
                    message=f"Deleted {len(deleted_paths)} context(s) at: {input_data.path}",
                    data={
                        "path": input_data.path,
                        "recursive": input_data.recursive,
                        "deleted_count": len(deleted_paths),
                        "deleted_paths": deleted_paths,
                    },
                )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to delete context: {str(e)}",
                error=str(e),
                data={"path": input_data.path},
            )
