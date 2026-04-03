"""Glance context tool - quick scan of context summaries."""

from pydantic import Field

from structure.core.interfaces.tool import (
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
        from uuid import UUID

        from structure.services.context.client import context_service_client

        try:
            workspace_id = UUID(input_data.workspace_id)
            prefix = input_data.prefix or ""

            result_data = await context_service_client.list_contexts(
                workspace_id=workspace_id, prefix=prefix, recursive=True
            )

            contexts = result_data.get("items", [])
            contexts = contexts[: input_data.limit]

            glances = [
                {
                    "path": (ctx.get("path") or "").lstrip("/"),
                    "glance": ctx.get("glance") or ctx.get("name") or "",
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
