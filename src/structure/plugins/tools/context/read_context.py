"""Read context tool - retrieve SQL-backed context by path."""

from typing import Literal

from pydantic import Field

from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)
from structure.extensions.database import get_session
from structure.plugins.tools.context._sql_context import (
    find_context_by_path,
    normalize_level,
    normalize_path,
    publish_context_using,
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
        run_id: str | None = Field(default=None, description="Run ID")
        user_id: str | None = Field(default=None, description="User ID")
        path: str = Field(
            description="Context path (e.g., 'tools/web_search' or 'knowledge/python_guide')"
        )
        level: Literal["glance", "overview", "detail"] = Field(
            default="detail",
            description="Disclosure level to return",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID

        try:
            workspace_id = UUID(input_data.workspace_id)
            level = normalize_level(input_data.level)
            normalized_path = normalize_path(input_data.path)

            async with get_session("structure") as session:
                ctx = await find_context_by_path(
                    session,
                    workspace_id,
                    normalized_path,
                    user_id=input_data.user_id,
                )
                if ctx is None:
                    return ToolOutputSchema(
                        success=False,
                        message=f"Context not found at path: {normalized_path}",
                        data={"path": normalized_path, "exists": False},
                    )

                data = ctx.disclose(level)
                await publish_context_using(
                    session,
                    workspace_id,
                    operation="read",
                    paths=[normalized_path],
                    level=level,
                    run_id=input_data.run_id,
                    user_id=input_data.user_id,
                )
                await session.commit()

            return ToolOutputSchema(
                success=True,
                message=f"Retrieved context: {normalized_path}",
                data=data,
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to read context: {e!s}",
                error=str(e),
                data={"path": input_data.path},
            )
