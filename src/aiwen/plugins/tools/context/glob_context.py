"""Glob context tool - query contexts using glob patterns."""

import re

from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Convert glob pattern to regex. * = single path segment, ** = any depth."""
    normalized = "/" + pattern.lstrip("/")
    parts = re.split(r"(\*\*|\*)", normalized)
    regex_parts = []
    for part in parts:
        if part == "**":
            regex_parts.append(".*")
        elif part == "*":
            regex_parts.append("[^/]*")
        else:
            regex_parts.append(re.escape(part))
    return re.compile("^" + "".join(regex_parts) + "$")


class GlobContextTool(InnerTool):
    """Query contexts using glob patterns (* for single level, ** for any depth)."""

    METADATA = ToolMetadata(
        name="glob_context",
        display_name="Glob Context",
        description="Query contexts using glob wildcards: * (single level), ** (any depth)",
        category="context",
        tags=["context", "glob", "search", "wildcard"],
        timeout=15,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        pattern: str = Field(
            description="Glob pattern (e.g., 'tools/*', 'tools/**', 'knowledge/*/docs')"
        )
        limit: int = Field(
            default=50,
            ge=1,
            le=200,
            description="Maximum number of results to return",
        )
        tags: list[str] | None = Field(
            default=None,
            description="Optional tag filters (only return contexts with ALL these tags)",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from sqlalchemy import select

        from aiwen.extensions.database import get_session
        from aiwen.models.context.workspace_context import WorkspaceContext

        try:
            pattern_regex = _glob_to_regex(input_data.pattern)

            # SQL prefix hint: narrow down rows before Python-level glob matching
            prefix_end = input_data.pattern.find("*")
            sql_prefix = None
            if prefix_end > 0:
                sql_prefix = "/" + input_data.pattern[:prefix_end].lstrip("/")

            async with get_session("aiwen") as db:
                stmt = (
                    select(WorkspaceContext)
                    .where(
                        WorkspaceContext.workspace_id == input_data.workspace_id,
                        WorkspaceContext.is_deleted == False,  # noqa: E712
                    )
                    .order_by(WorkspaceContext.path)
                )

                if sql_prefix:
                    stmt = stmt.where(WorkspaceContext.path.like(f"{sql_prefix}%"))

                result = await db.execute(stmt)
                contexts = result.scalars().all()

            # Apply glob filter in Python
            matched = [ctx for ctx in contexts if ctx.path and pattern_regex.match(ctx.path)]

            # Apply tag filter
            if input_data.tags:
                matched = [ctx for ctx in matched if ctx.has_all_tags(input_data.tags)]

            matched = matched[: input_data.limit]

            items = [ctx.disclose("glance") for ctx in matched]
            paths = [(ctx.path or "").lstrip("/") for ctx in matched]

            return ToolOutputSchema(
                success=True,
                message=f"Found {len(items)} contexts matching pattern: {input_data.pattern}",
                data={
                    "pattern": input_data.pattern,
                    "count": len(items),
                    "paths": paths,
                    "contexts": items,
                    "tags_filter": input_data.tags,
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Glob query failed: {e!s}",
                error=str(e),
                data={"pattern": input_data.pattern},
            )
