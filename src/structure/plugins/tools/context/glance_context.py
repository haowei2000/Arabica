"""Glance context tool - quick scan of SQL-backed context summaries."""

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


class GlanceContextTool(InnerTool):
    """Quick scan of context glances."""

    METADATA = ToolMetadata(
        name="glance_context",
        display_name="Glance Context",
        description="Quick scan showing one-line summaries of contexts",
        category="context",
        tags=["context", "glance", "scan", "summary", "quick"],
        timeout=10,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        run_id: str | None = Field(default=None, description="Run ID")
        user_id: str | None = Field(default=None, description="User ID")
        prefix: str | None = Field(
            default=None,
            description="Optional path prefix to scan (None for all contexts)",
        )
        min_rating: float | None = Field(
            default=None,
            ge=-1.0,
            le=1.0,
            description="Optional minimum average context rating",
        )
        limit: int = Field(default=100, ge=1, le=500, description="Maximum glances")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID

        try:
            workspace_id = UUID(input_data.workspace_id)
            prefix = normalize_path(input_data.prefix) if input_data.prefix else None

            async with get_session("structure") as session:
                glances = await list_contexts(
                    session,
                    workspace_id,
                    prefix=prefix,
                    level="glance",
                    user_id=input_data.user_id,
                    recursive=True,
                    limit=input_data.limit,
                    min_rating=input_data.min_rating,
                )
                paths = [ctx.get("path", "") for ctx in glances if ctx.get("path")]
                await publish_context_using(
                    session,
                    workspace_id,
                    operation="glance",
                    paths=paths,
                    level="glance",
                    run_id=input_data.run_id,
                    user_id=input_data.user_id,
                    meta={"prefix": prefix},
                )
                await session.commit()

            return ToolOutputSchema(
                success=True,
                message=f"Scanned {len(glances)} contexts",
                data={"prefix": prefix, "count": len(glances), "glances": glances},
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Glance scan failed: {e!s}",
                error=str(e),
                data={"prefix": input_data.prefix},
            )
