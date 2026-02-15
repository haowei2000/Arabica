"""Read context tool - retrieve context by path with progressive disclosure."""

from typing import Literal

from pydantic import Field

from aiwen.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class ReadContextTool(InnerTool):
    """Read context content with progressive disclosure (glance/overview/detail)."""

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
        level: Literal["glance", "overview", "detail"] = Field(
            default="overview",
            description="Detail level: glance (quick), overview (summary), detail (full)",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from aiwen.extensions.database import get_session
        from aiwen.services.workspace_context_service import WorkspaceContextService

        try:
            async with get_session("aiwen") as db:
                # Initialize service
                service = WorkspaceContextService(db, input_data.workspace_id)
                await service.load()

                # Get context with specified detail level
                result = await service.get(input_data.path, level=input_data.level)

                if result is None:
                    return ToolOutputSchema(
                        success=False,
                        message=f"Context not found at path: {input_data.path}",
                        data={"path": input_data.path, "exists": False},
                    )

                return ToolOutputSchema(
                    success=True,
                    message=f"Retrieved context: {input_data.path} ({input_data.level})",
                    data={
                        "path": input_data.path,
                        "level": input_data.level,
                        "context": result,
                    },
                )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to read context: {str(e)}",
                error=str(e),
                data={"path": input_data.path},
            )
