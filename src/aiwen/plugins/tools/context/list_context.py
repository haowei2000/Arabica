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
        from aiwen.extensions.database import get_session
        from aiwen.frameworks.context import DetailLevel
        from aiwen.utils.workspace_context_cache import get_cached_workspace_context

        try:
            async with get_session("aiwen") as db:
                # Initialize service with caching
                service = await get_cached_workspace_context(db, input_data.workspace_id)

                # Query based on mode
                if input_data.mode == "children":
                    results = await service.children(input_data.path)
                else:
                    results = await service.descendants(input_data.path)

                # Limit results
                results = results.limit(input_data.limit)

                # Convert to detail level
                contexts = results.disclose_all(DetailLevel.GLANCE)

                # Get paths
                paths = results.paths()

                return ToolOutputSchema(
                    success=True,
                    message=f"Found {len(contexts)} {input_data.mode} under: {input_data.path}",
                    data={
                        "path": input_data.path,
                        "mode": input_data.mode,
                        "count": len(contexts),
                        "paths": paths,
                        "contexts": contexts,
                    },
                )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"List failed: {e!s}",
                error=str(e),
                data={"path": input_data.path, "mode": input_data.mode},
            )
