"""Get run history tool."""

from pydantic import Field

from aiwen.interfaces.tool import InnerTool, ToolInputSchema, ToolMetadata, ToolOutputSchema


class GetRunHistoryTool(InnerTool):
    """Get recent run history for a workspace"""

    METADATA = ToolMetadata(
        name="get_run_history",
        display_name="Get Run History",
        description="Get recent run history for a workspace with optional status filter",
        category="utility",
        tags=["runs", "history", "workspace"],
        timeout=15,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="The workspace UUID")
        limit: int = Field(
            default=10, ge=1, le=100, description="Maximum runs to return"
        )
        status: str | None = Field(
            default=None,
            description="Filter by status (running, completed, failed, etc.)",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from sqlalchemy import select

        from aiwen.extensions.database import get_session
        from aiwen.models.runs.run import Run

        async with get_session("aiwen") as db:
            query = (
                select(Run)
                .where(Run.workspace_id == input_data.workspace_id)
                .order_by(Run.created_at.desc())
                .limit(input_data.limit)
            )

            if input_data.status:
                query = query.where(Run.status == input_data.status)

            result = await db.execute(query)
            runs = result.scalars().all()

            return ToolOutputSchema(
                success=True,
                message=f"Retrieved {len(runs)} runs for workspace",
                data={
                    "runs": [
                        {
                            "id": str(run.id),
                            "status": run.status,
                            "trigger_type": run.trigger_type,
                            "created_at": run.created_at.isoformat()
                            if run.created_at
                            else None,
                            "completed_at": run.completed_at.isoformat()
                            if run.completed_at
                            else None,
                            "input_preview": str(run.input_data)[:100]
                            if run.input_data
                            else None,
                        }
                        for run in runs
                    ],
                    "total": len(runs),
                    "workspace_id": input_data.workspace_id,
                },
            )
