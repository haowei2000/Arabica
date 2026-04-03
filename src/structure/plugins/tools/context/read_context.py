from pydantic import Field

from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class ReadContextTool(InnerTool):
    METADATA = ToolMetadata(
        name="read_context",
        display_name="Read Context",
        description="Read a single context by path with progressive disclosure levels",
        category="context",
        tags=["context", "read", "get"],
        timeout=10,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        path: str = Field(
            description="Context path (e.g., 'tools/web_search' or 'knowledge/python_guide')"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID

        from structure.services.context.client import context_service_client

        try:
            workspace_id = UUID(input_data.workspace_id)
            ctx_data = await context_service_client.get_context(
                workspace_id=workspace_id, path=input_data.path
            )

            if ctx_data is None:
                return ToolOutputSchema(
                    success=False,
                    message=f"Context not found at path: {input_data.path}",
                    data={"path": input_data.path, "exists": False},
                )

            return ToolOutputSchema(
                success=True,
                message=f"Retrieved context: {input_data.path}",
                data={
                    "path": input_data.path,
                    "content": ctx_data.get("content"),
                    "content_type": ctx_data.get("content_type"),
                    "glance": ctx_data.get("glance"),
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to read context: {e!s}",
                error=str(e),
                data={"path": input_data.path},
            )
