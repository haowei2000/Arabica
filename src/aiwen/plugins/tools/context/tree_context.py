"""Tree context tool - get hierarchical tree structure."""

from typing import Literal

from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class TreeContextTool(InnerTool):
    """Get context hierarchy as a tree structure with nested children."""

    METADATA = ToolMetadata(
        name="tree_context",
        display_name="Tree Context",
        description="Get hierarchical tree structure of contexts with nested children",
        category="context",
        tags=["context", "tree", "hierarchy", "structure"],
        timeout=20,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        root: str | None = Field(
            default=None,
            description="Root path to build tree from (None for full tree)",
        )
        level: Literal["glance", "overview", "detail"] = Field(
            default="overview",
            description="Detail level for each node",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from aiwen.extensions.database import get_session
        from aiwen.utils.workspace_context_cache import get_cached_workspace_context

        try:
            async with get_session("aiwen") as db:
                # Initialize service with caching
                service = await get_cached_workspace_context(db, input_data.workspace_id)

                # Get tree structure
                tree = await service.tree(
                    root=input_data.root,
                    level=input_data.level,
                )

                # Count nodes
                def count_nodes(node):
                    count = 1
                    for child in node.get("children", []):
                        count += count_nodes(child)
                    return count

                total_nodes = count_nodes(tree) if tree else 0

                return ToolOutputSchema(
                    success=True,
                    message=f"Retrieved tree structure ({total_nodes} nodes)",
                    data={
                        "root": input_data.root or "/",
                        "level": input_data.level,
                        "total_nodes": total_nodes,
                        "tree": tree,
                    },
                )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Tree query failed: {e!s}",
                error=str(e),
                data={"root": input_data.root},
            )
