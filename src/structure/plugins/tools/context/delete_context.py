"""Delete context tool - remove SQL-backed workspace context."""

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
    delete_workspace_context,
    normalize_path,
    publish_context_event,
)


class DeleteContextTool(InnerTool):
    """Delete a context entry, optionally including descendants."""

    METADATA = ToolMetadata(
        name="delete_context",
        display_name="Delete Context",
        description="Delete context entry. Can delete recursively with descendants.",
        category="context",
        tags=["context", "delete", "remove", "rm"],
        timeout=15,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        run_id: str | None = Field(default=None, description="Run ID")
        user_id: str | None = Field(default=None, description="User ID")
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
        from uuid import UUID

        try:
            normalized_path = normalize_path(input_data.path)
            if not input_data.confirm:
                return ToolOutputSchema(
                    success=False,
                    message="Delete not confirmed. Set confirm=true to proceed.",
                    data={
                        "path": normalized_path,
                        "recursive": input_data.recursive,
                        "confirmed": False,
                    },
                )

            workspace_id = UUID(input_data.workspace_id)
            async with get_session("structure") as session:
                deleted_count = await delete_workspace_context(
                    session,
                    workspace_id,
                    path=normalized_path,
                    recursive=input_data.recursive,
                )
                if deleted_count == 0:
                    return ToolOutputSchema(
                        success=False,
                        message=f"Context not found at path: {normalized_path}",
                        data={"path": normalized_path, "exists": False},
                    )

                await publish_context_event(
                    session,
                    EventType.CONTEXT_DELETED,
                    workspace_id,
                    run_id=input_data.run_id,
                    user_id=input_data.user_id,
                    payload={
                        "path": normalized_path,
                        "recursive": input_data.recursive,
                        "deleted_count": deleted_count,
                    },
                )
                await session.commit()

            return ToolOutputSchema(
                success=True,
                message=f"Deleted context at: {normalized_path}",
                data={
                    "path": normalized_path,
                    "recursive": input_data.recursive,
                    "deleted_count": deleted_count,
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to delete context: {e!s}",
                error=str(e),
                data={"path": input_data.path},
            )
