"""Hybrid query context tool."""

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
    normalize_level,
    normalize_path,
    publish_context_using,
    query_contexts,
)


class QueryContextTool(InnerTool):
    """Hybrid prefix/text/rating query over scoped SQL-backed context."""

    METADATA = ToolMetadata(
        name="query_context",
        display_name="Query Context",
        description="Hybrid query over context using optional path prefix and rating-aware ranking",
        category="context",
        tags=["context", "query", "hybrid", "rating"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        run_id: str | None = Field(default=None, description="Run ID")
        user_id: str | None = Field(default=None, description="User ID")
        query: str = Field(description="Natural-language or keyword query")
        prefix: str | None = Field(
            default=None,
            description="Optional context path prefix to restrict the query",
        )
        level: Literal["glance", "overview", "detail"] = Field(
            default="overview",
            description="Disclosure level to return",
        )
        top_k: int = Field(default=10, ge=1, le=50, description="Number of results")
        min_rating: float | None = Field(
            default=None,
            ge=-1.0,
            le=1.0,
            description="Optional minimum average context rating",
        )
        alpha: float = Field(
            default=0.7,
            ge=0.0,
            le=1.0,
            description="Weight for lexical relevance vs rating quality",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID

        try:
            workspace_id = UUID(input_data.workspace_id)
            level = normalize_level(input_data.level)
            prefix = normalize_path(input_data.prefix) if input_data.prefix else None

            async with get_session("structure") as session:
                contexts = await query_contexts(
                    session,
                    workspace_id,
                    query=input_data.query,
                    prefix=prefix,
                    level=level,
                    user_id=input_data.user_id,
                    top_k=input_data.top_k,
                    min_rating=input_data.min_rating,
                    alpha=input_data.alpha,
                )
                paths = [ctx.get("path", "") for ctx in contexts if ctx.get("path")]
                await publish_context_using(
                    session,
                    workspace_id,
                    operation="query",
                    paths=paths,
                    level=level,
                    run_id=input_data.run_id,
                    user_id=input_data.user_id,
                    meta={"query": input_data.query, "prefix": prefix},
                )
                await session.commit()

            return ToolOutputSchema(
                success=True,
                message=f"Found {len(contexts)} contexts for query",
                data={
                    "query": input_data.query,
                    "prefix": prefix,
                    "count": len(contexts),
                    "contexts": contexts,
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Context query failed: {e!s}",
                error=str(e),
                data={"query": input_data.query, "prefix": input_data.prefix},
            )
