"""Get workspace info tool."""

from pydantic import Field

from aiwen.interfaces.tool import InnerTool, ToolInputSchema, ToolMetadata, ToolOutputSchema


class GetWorkspaceInfoTool(InnerTool):
    """Get detailed information about a workspace"""

    METADATA = ToolMetadata(
        name="get_workspace_info",
        display_name="Get Workspace Info",
        description="Get detailed information about a workspace including name, owner, settings",
        category="utility",
        tags=["workspace", "info", "metadata"],
        timeout=10,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="The workspace UUID")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from sqlalchemy import func, select

        from aiwen.extensions.database import get_session
        from aiwen.models.runs.run import Run
        from aiwen.models.workspaces.workspace import Workspace

        async with get_session("aiwen") as db:
            result = await db.execute(
                select(Workspace).where(Workspace.id == input_data.workspace_id)
            )
            workspace = result.scalar_one_or_none()

            if not workspace:
                return ToolOutputSchema(
                    success=False,
                    message=f"Workspace not found: {input_data.workspace_id}",
                    error=f"No workspace found with ID {input_data.workspace_id}",
                )

            run_count_result = await db.execute(
                select(func.count(Run.id)).where(
                    Run.workspace_id == input_data.workspace_id
                )
            )
            run_count = run_count_result.scalar() or 0

            return ToolOutputSchema(
                success=True,
                message=f"Retrieved workspace info for {workspace.name}",
                data={
                    "id": str(workspace.id),
                    "name": workspace.name,
                    "description": workspace.description,
                    "owner_id": str(workspace.owner_id) if workspace.owner_id else None,
                    "app_id": str(workspace.app_id) if workspace.app_id else None,
                    "settings": workspace.settings or {},
                    "run_count": run_count,
                    "created_at": workspace.created_at.isoformat()
                    if workspace.created_at
                    else None,
                },
            )
