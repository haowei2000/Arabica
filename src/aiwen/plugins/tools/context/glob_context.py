"""Glob context tool - query contexts using glob patterns."""

from typing import Literal

from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


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
        level: Literal["glance", "overview", "detail"] = Field(
            default="glance",
            description="Detail level for results",
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
        from aiwen.extensions.database import get_session
        from aiwen.frameworks.context import DetailLevel
        from aiwen.utils.workspace_context_cache import get_cached_workspace_context

        try:
            async with get_session("aiwen") as db:
                # Initialize service with caching
                service = await get_cached_workspace_context(db, input_data.workspace_id)

                # Glob query
                results = await service.glob(input_data.pattern)

                # Apply tag filter if provided
                if input_data.tags:
                    results = results.filter_tags(input_data.tags)

                # Limit results
                results = results.limit(input_data.limit)

                # Convert to detail level
                level_enum = DetailLevel.from_str(input_data.level)
                contexts = results.disclose_all(level_enum)

                # Get paths for summary
                paths = results.paths()

                return ToolOutputSchema(
                    success=True,
                    message=f"Found {len(contexts)} contexts matching pattern: {input_data.pattern}",
                    data={
                        "pattern": input_data.pattern,
                        "count": len(contexts),
                        "paths": paths,
                        "contexts": contexts,
                        "level": input_data.level,
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
