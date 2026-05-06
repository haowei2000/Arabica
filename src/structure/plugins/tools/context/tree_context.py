"""Tree context tool - get hierarchical tree structure."""

from typing import Any

from pydantic import Field

from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)
from structure.extensions.database import get_session
from structure.plugins.tools.context._sql_context import (
    list_contexts,
    normalize_path,
    publish_context_using,
)


def _disclose_overview(ctx: dict) -> dict[str, Any]:
    """Progressive disclosure for dictionary-based context."""
    path = ctx.get("path")
    name = ctx.get("name") or (path.rsplit("/", 1)[-1] if path else "unnamed")
    result: dict[str, Any] = {"path": path, "name": name}

    # Level 1: Glance
    if ctx.get("glance"):
        result["glance"] = ctx["glance"]
    elif ctx.get("summary"):
        summ = ctx["summary"]
        result["glance"] = summ[:100] + "..." if len(summ) > 100 else summ
    elif ctx.get("content"):
        cont = ctx["content"]
        result["glance"] = cont[:50] + "..." if len(cont) > 50 else cont
    else:
        result["glance"] = f"{name} ({ctx.get('content_type') or 'unknown'})"

    # Level 2: Overview
    if ctx.get("summary"):
        result["overview"] = ctx["summary"]
    result["content_type"] = ctx.get("content_type")
    if ctx.get("size_bytes") is not None:
        result["size_bytes"] = ctx["size_bytes"]
    if ctx.get("tags"):
        result["tags"] = ctx["tags"]

    return result


def _build_tree(contexts: list[dict], root_normalized: str | None) -> dict:
    """Build nested tree from flat context list."""
    node_map: dict[str, dict] = {}
    for ctx in sorted(contexts, key=lambda c: c.get("path") or ""):
        path = ctx.get("path")
        if not path:
            continue
        data = _disclose_overview(ctx)
        data["children"] = []
        node_map[path] = data

    for path in sorted(node_map.keys()):
        # "/" in path[1:] checks if there's a slash after the leading one
        parent = path.rsplit("/", 1)[0] if "/" in path[1:] else None
        if parent and parent in node_map:
            node_map[parent]["children"].append(node_map[path])

    if root_normalized:
        if root_normalized in node_map:
            return node_map[root_normalized]
        # root has no record itself — collect its direct children
        prefix = root_normalized + "/"
        child_depth = root_normalized.count("/") + 1
        orphans = [
            node
            for path, node in sorted(node_map.items())
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
        run_id: str | None = Field(default=None, description="Run ID")
        user_id: str | None = Field(default=None, description="User ID")
        root: str | None = Field(
            default=None,
            description="Root path to build tree from (None for full tree)",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID

        try:
            workspace_id = UUID(input_data.workspace_id)
            root_normalized = normalize_path(input_data.root) if input_data.root else ""

            async with get_session("structure") as session:
                contexts = await list_contexts(
                    session,
                    workspace_id,
                    prefix=root_normalized or None,
                    level="overview",
                    user_id=input_data.user_id,
                    recursive=True,
                    limit=500,
                )
                paths = [ctx.get("path", "") for ctx in contexts if ctx.get("path")]
                await publish_context_using(
                    session,
                    workspace_id,
                    operation="tree",
                    paths=paths,
                    level="overview",
                    run_id=input_data.run_id,
                    user_id=input_data.user_id,
                    meta={"root": root_normalized or "/"},
                )
                await session.commit()

            tree = _build_tree(contexts, root_normalized or None)
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
