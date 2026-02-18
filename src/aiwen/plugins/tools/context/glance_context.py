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
        from aiwen.extensions.database import get_session
        from aiwen.utils.workspace_context_cache import get_cached_workspace_context

        try:
            async with get_session("aiwen") as db:
                # Initialize service with caching
                service = await get_cached_workspace_context(db, input_data.workspace_id)

                # Get glances
                glances = await service.glance(prefix=input_data.prefix)

                # Limit results
                glances = glances[: input_data.limit]

                # Parse glances into structured format
                structured_glances = []
                for glance_line in glances:
                    if " → " in glance_line:
                        path, glance_text = glance_line.split(" → ", 1)
                        structured_glances.append({
                            "path": path.strip(),
                            "glance": glance_text.strip(),
                        })
                    else:
                        structured_glances.append({
                            "path": "",
                            "glance": glance_line.strip(),
                        })

                return ToolOutputSchema(
                    success=True,
                    message=f"Scanned {len(glances)} contexts",
                    data={
                        "prefix": input_data.prefix,
                        "count": len(glances),
                        "glances": structured_glances,
                        "raw_glances": glances,  # Original format for display
                    },
                )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Glance scan failed: {e!s}",
                error=str(e),
                data={"prefix": input_data.prefix},
            )
