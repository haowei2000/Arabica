"""List context tool - list direct children or all descendants."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from structure.models.context.workspace_context import WorkspaceContext


def _build_md_tree(contexts: list[dict], root_path: str) -> str:
    """Build a markdown tree string from a list of contexts."""
    lines: list[str] = []
    root_depth = root_path.rstrip("/").count("/")

    for ctx in contexts:
        path = ctx.get("path", "").rstrip("/")
        depth = path.count("/") - root_depth - 1
        indent = "  " * max(0, depth)
        name = path.rsplit("/", 1)[-1]
        lines.append(f"{indent}- {name}")

    return "\n".join(lines)


from pydantic import Field  # noqa: E402

from structure.core.interfaces.tool import (  # noqa: E402
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
        from uuid import UUID

        from structure.services.context.client import context_service_client

        try:
            workspace_id = UUID(input_data.workspace_id)
            recursive = input_data.mode == "descendants"

            result_data = await context_service_client.list_contexts(
                workspace_id=workspace_id, prefix=input_data.path, recursive=recursive
            )

            contexts = result_data.get("items", [])
            contexts = contexts[: input_data.limit]

            tree = _build_md_tree(contexts, input_data.path)

            return ToolOutputSchema(
                success=True,
                message=f"Found {len(contexts)} {input_data.mode} under: {input_data.path}",
                data={"tree": tree},
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"List failed: {e!s}",
                error=str(e),
                data={"path": input_data.path, "mode": input_data.mode},
            )
