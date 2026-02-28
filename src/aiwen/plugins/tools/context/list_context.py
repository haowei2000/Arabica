"""List context tool - list direct children or all descendants."""

from typing import Literal

from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class ListContextTool(InnerTool):
    """List contexts - direct children or all descendants under a path."""

    METADATA = ToolMetadata(
        name="list_context",
        display_name="List Context",
        description="List contexts under a path: children (1 level) or descendants (all levels)",
        category="context",
        tags=["context", "list", "children", "ls"],
        timeout=15,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        path: str = Field(
            description="Parent path to list from (e.g., 'tools', 'knowledge')"
        )
        mode: Literal["children", "descendants"] = Field(
            default="children",
            description="children: direct children only, descendants: all nested items",
        )
        limit: int = Field(
            default=100,
            ge=1,
            le=500,
            description="Maximum number of results",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from sqlalchemy import select

        from aiwen.extensions.database import get_session
        from aiwen.models.context.workspace_context import WorkspaceContext

        try:
            normalized_path = "/" + input_data.path.strip("/")
            prefix = normalized_path + "/"
            child_depth = normalized_path.count("/") + 1  # slash count of direct children

            async with get_session("aiwen") as db:
                stmt = (
                    select(WorkspaceContext)
                    .where(
                        WorkspaceContext.workspace_id == input_data.workspace_id,
                        WorkspaceContext.path.like(f"{prefix}%"),
                        WorkspaceContext.is_deleted == False,  # noqa: E712
                    )
                    .order_by(WorkspaceContext.path)
                )
                result = await db.execute(stmt)
                all_descendants = result.scalars().all()

            if input_data.mode == "children":
                contexts = [
                    ctx for ctx in all_descendants
                    if ctx.path and ctx.path.count("/") == child_depth
                ]
            else:
                contexts = list(all_descendants)

            contexts = contexts[: input_data.limit]

            items = [ctx.disclose("glance") for ctx in contexts]
            paths = [(ctx.path or "").lstrip("/") for ctx in contexts]

            return ToolOutputSchema(
                success=True,
                message=f"Found {len(items)} {input_data.mode} under: {input_data.path}",
                data={
                    "path": input_data.path,
                    "mode": input_data.mode,
                    "count": len(items),
                    "paths": paths,
                    "contexts": items,
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"List failed: {e!s}",
                error=str(e),
                data={"path": input_data.path, "mode": input_data.mode},
            )
