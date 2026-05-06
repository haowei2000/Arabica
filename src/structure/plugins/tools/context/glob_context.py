"""Glob context tool - query SQL-backed contexts using path wildcards."""

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
    glob_contexts,
    normalize_level,
    publish_context_using,
)


class GlobContextTool(InnerTool):
    """Query contexts using glob patterns (* for one level, ** for any depth)."""

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
        run_id: str | None = Field(default=None, description="Run ID")
        user_id: str | None = Field(default=None, description="User ID")
        pattern: str = Field(
            description="Glob pattern (e.g., 'tools/*', 'tools/**', 'knowledge/*/docs')"
        )
        level: Literal["glance", "overview", "detail"] = Field(
            default="glance",
            description="Disclosure level to return",
        )
        min_rating: float | None = Field(
            default=None,
            ge=-1.0,
            le=1.0,
            description="Optional minimum average context rating",
        )
        limit: int = Field(default=50, ge=1, le=200, description="Maximum results")
        tags: list[str] | None = Field(
            default=None,
            description="Optional tag filters (contexts must have all tags)",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID

        try:
            workspace_id = UUID(input_data.workspace_id)
            level = normalize_level(input_data.level)

            async with get_session("structure") as session:
                contexts = await glob_contexts(
                    session,
                    workspace_id,
                    pattern=input_data.pattern,
                    level=level,
                    user_id=input_data.user_id,
                    limit=input_data.limit,
                    tags=input_data.tags,
                    min_rating=input_data.min_rating,
                )
                paths = [ctx.get("path", "") for ctx in contexts if ctx.get("path")]
                await publish_context_using(
                    session,
                    workspace_id,
                    operation="glob",
                    paths=paths,
                    level=level,
                    run_id=input_data.run_id,
                    user_id=input_data.user_id,
                    meta={"pattern": input_data.pattern},
                )
                await session.commit()

            return ToolOutputSchema(
                success=True,
                message=f"Found {len(contexts)} contexts matching: {input_data.pattern}",
                data={
                    "pattern": input_data.pattern,
                    "count": len(contexts),
                    "paths": [p.lstrip("/") for p in paths],
                    "contexts": contexts,
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
