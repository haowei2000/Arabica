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
        from uuid import UUID
        from aiwen.services.context.client import context_service_client

        try:
            workspace_id = UUID(input_data.workspace_id)
            pattern_regex = _glob_to_regex(input_data.pattern)

            # SQL prefix hint: narrow down rows before Python-level glob matching
            prefix_end = input_data.pattern.find("*")
            sql_prefix = ""
            if prefix_end > 0:
                sql_prefix = input_data.pattern[:prefix_end].rstrip("/")

            result_data = await context_service_client.list_contexts(
                workspace_id=workspace_id,
                prefix=sql_prefix,
                recursive=True
            )
            
            contexts = result_data.get("items", [])

            # Apply glob filter in Python
            matched = [ctx for ctx in contexts if ctx.get("path") and pattern_regex.match(ctx.get("path"))]

            # Apply tag filter
            if input_data.tags:
                matched = [
                    ctx for ctx in matched 
                    if ctx.get("tags") and all(tag in ctx.get("tags", []) for tag in input_data.tags)
                ]

            matched = matched[: input_data.limit]

            def disclose_glance(ctx):
                path = ctx.get("path")
                name = ctx.get("name") or (path.rsplit("/", 1)[-1] if path else "unnamed")
                res = {"path": path, "name": name}
                
                if ctx.get("glance"):
                    res["glance"] = ctx["glance"]
                elif ctx.get("summary"):
                    summ = ctx["summary"]
                    res["glance"] = summ[:100] + "..." if len(summ) > 100 else summ
                elif ctx.get("content"):
                    cont = ctx["content"]
                    res["glance"] = cont[:50] + "..." if len(cont) > 50 else cont
                else:
                    res["glance"] = f"{name} ({ctx.get('content_type') or 'unknown'})"
                return res

            items = [disclose_glance(ctx) for ctx in matched]
            paths = [(ctx.get("path") or "").lstrip("/") for ctx in matched]

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
