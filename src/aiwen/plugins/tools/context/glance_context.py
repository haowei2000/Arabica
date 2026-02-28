"""Glance context tool - quick scan of context summaries."""

from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class GlanceContextTool(InnerTool):
    """Quick scan of context glances - one-line summaries for rapid browsing."""

    METADATA = ToolMetadata(
        name="glance_context",
        display_name="Glance Context",
        description="Quick scan showing one-line summaries of all contexts (fastest query)",
        category="context",
        tags=["context", "glance", "scan", "summary", "quick"],
        timeout=10,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        prefix: str | None = Field(
            default=None,
            description="Optional path prefix to scan (None for all contexts)",
        )
        limit: int = Field(
            default=100,
            ge=1,
            le=500,
            description="Maximum number of glances to return",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from sqlalchemy import select

        from aiwen.extensions.database import get_session
        from aiwen.models.context.workspace_context import WorkspaceContext

        try:
            async with get_session("aiwen") as db:
                stmt = (
                    select(WorkspaceContext)
                    .where(
                        WorkspaceContext.workspace_id == input_data.workspace_id,
                        WorkspaceContext.is_deleted == False,  # noqa: E712
                    )
                    .order_by(WorkspaceContext.path)
                    .limit(input_data.limit)
                )

                if input_data.prefix:
                    normalized_prefix = "/" + input_data.prefix.lstrip("/")
                    stmt = stmt.where(
                        WorkspaceContext.path.like(f"{normalized_prefix}%")
                    )

                result = await db.execute(stmt)
                contexts = result.scalars().all()

            glances = [
                {
                    "path": (ctx.path or "").lstrip("/"),
                    "glance": ctx.glance or ctx.name,
                }
                for ctx in contexts
            ]

            return ToolOutputSchema(
                success=True,
                message=f"Scanned {len(glances)} contexts",
                data={
                    "prefix": input_data.prefix,
                    "count": len(glances),
                    "glances": glances,
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Glance scan failed: {e!s}",
                error=str(e),
                data={"prefix": input_data.prefix},
            )
