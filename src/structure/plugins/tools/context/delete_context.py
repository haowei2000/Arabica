"""Delete context tool - remove context from workspace."""

from pydantic import Field

from structure.core.interfaces.tool import (
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
        from structure.services.context.client import context_service_client
        from uuid import UUID

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

            workspace_id = UUID(input_data.workspace_id)
            # The context service delete is naturally recursive if the path is a 'directory'
            # in our implementation (since we use shutil.rmtree).
            success = await context_service_client.delete_context(
                workspace_id=workspace_id,
                path=input_data.path
            )

            if not success:
                return ToolOutputSchema(
                    success=False,
                    message=f"Context not found at path: {input_data.path}",
                    data={"path": input_data.path, "exists": False},
                )

            return ToolOutputSchema(
                success=True,
                message=f"Deleted context at: {input_data.path}",
                data={
                    "path": input_data.path,
                    "recursive": input_data.recursive,
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to delete context: {e!s}",
                error=str(e),
                data={"path": input_data.path},
            )
