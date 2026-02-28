from pydantic import Field

from aiwen.core.interfaces.tool import (
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

            return ToolOutputSchema(
                success=True,
                message=f"Retrieved context: {input_data.path}",
                data={
                    "path": input_data.path,
                    "context": ctx.disclose("detail"),
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to read context: {e!s}",
                error=str(e),
                data={"path": input_data.path},
            )
