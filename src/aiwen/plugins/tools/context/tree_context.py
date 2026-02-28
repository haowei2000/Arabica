"""Tree context tool - get hierarchical tree structure."""

from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


def _build_tree(contexts: list, root_normalized: str | None, level: str) -> dict:
    """Build nested tree from flat DB context list."""
    node_map: dict[str, dict] = {}
    for ctx in sorted(contexts, key=lambda c: c.path or ""):
        if not ctx.path:
            continue
        data = ctx.disclose(level)
        data["children"] = []
        node_map[ctx.path] = data

    for path in sorted(node_map.keys()):
        # "/" in path[1:] checks if there's a slash after the leading one
        parent = path.rsplit("/", 1)[0] if "/" in path[1:] else None
        if parent and parent in node_map:
            node_map[parent]["children"].append(node_map[path])

    if root_normalized:
        if root_normalized in node_map:
            return node_map[root_normalized]
        # root has no DB record itself — collect its direct children
        prefix = root_normalized + "/"
        child_depth = root_normalized.count("/") + 1
        orphans = [
            node for path, node in sorted(node_map.items())
            if path.startswith(prefix) and path.count("/") == child_depth
        ]
        return {"path": root_normalized, "children": orphans}

    top_level = [node for path, node in sorted(node_map.items()) if "/" not in path[1:]]
    return {"path": "/", "children": top_level}


def _count_nodes(node: dict) -> int:
    return 1 + sum(_count_nodes(c) for c in node.get("children", []))


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

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from sqlalchemy import or_, select

        from aiwen.extensions.database import get_session
        from aiwen.models.context.workspace_context import WorkspaceContext

        try:
            root_normalized = ("/" + input_data.root.strip("/")) if input_data.root else None

            async with get_session("aiwen") as db:
                stmt = (
                    select(WorkspaceContext)
                    .where(
                        WorkspaceContext.workspace_id == input_data.workspace_id,
                        WorkspaceContext.is_deleted == False,  # noqa: E712
                    )
                    .order_by(WorkspaceContext.path)
                )

                if root_normalized:
                    stmt = stmt.where(
                        or_(
                            WorkspaceContext.path == root_normalized,
                            WorkspaceContext.path.like(f"{root_normalized}/%"),
                        )
                    )

                result = await db.execute(stmt)
                contexts = result.scalars().all()

            tree = _build_tree(contexts, root_normalized, "overview")
            total_nodes = _count_nodes(tree) if tree else 0

            return ToolOutputSchema(
                success=True,
                message=f"Retrieved tree structure ({total_nodes} nodes)",
                data={
                    "root": input_data.root or "/",
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
