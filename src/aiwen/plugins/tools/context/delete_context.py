"""Delete context tool - remove context from workspace."""

from pydantic import Field

from aiwen.core.interfaces.tool import (
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
        path: str = Field(description="Context path to delete")
        recursive: bool = Field(
            default=False,
            description="If true, delete all descendants too (like rm -r)",
        )
        confirm: bool = Field(
            default=False,
            description="Must be true to actually delete (safety check)",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from sqlalchemy import or_, select

        from aiwen.extensions.database import get_session
        from aiwen.models.context.workspace_context import WorkspaceContext

        try:
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

            normalized_path = "/" + input_data.path.lstrip("/")
            async with get_session("aiwen") as db:
                if input_data.recursive:
                    stmt = select(WorkspaceContext).where(
                        WorkspaceContext.workspace_id == input_data.workspace_id,
                        WorkspaceContext.is_deleted == False,  # noqa: E712
                        or_(
                            WorkspaceContext.path == normalized_path,
                            WorkspaceContext.path.like(f"{normalized_path}/%"),
                        ),
                    )
                else:
                    stmt = select(WorkspaceContext).where(
                        WorkspaceContext.workspace_id == input_data.workspace_id,
                        WorkspaceContext.path == normalized_path,
                        WorkspaceContext.is_deleted == False,  # noqa: E712
                    )

                result = await db.execute(stmt)
                contexts = result.scalars().all()

                if not contexts:
                    return ToolOutputSchema(
                        success=False,
                        message=f"Context not found at path: {input_data.path}",
                        data={"path": input_data.path, "exists": False},
                    )

                deleted_paths = []
                for ctx in contexts:
                    ctx.is_deleted = True
                    deleted_paths.append(ctx.path)

                await db.commit()

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
                message=f"Failed to delete context: {e!s}",
                error=str(e),
                data={"path": input_data.path},
            )
