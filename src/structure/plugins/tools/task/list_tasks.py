"""List tasks tool - list tasks for a workspace or run."""

from pydantic import Field

from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class ListTasksTool(InnerTool):
    """List tasks in a workspace, optionally filtered by run or status."""

    METADATA = ToolMetadata(
        name="list_tasks",
        display_name="List Tasks",
        description=(
            "List tasks in a workspace, optionally filtered by run ID, status, or parent task. "
            "Returns task IDs, titles, statuses, and priorities."
        ),
        category="task",
        tags=["task", "list", "search", "plan"],
        timeout=10,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        run_id: str | None = Field(default=None, description="Filter by run ID")
        status: str | None = Field(
            default=None,
            description="Filter by status: pending, in_progress, done, failed, cancelled",
        )
        parent_task_id: str | None = Field(
            default=None, description="Filter by parent task ID (to list subtasks)"
        )
        limit: int = Field(default=20, description="Maximum number of results (1-100)", ge=1, le=100)

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID

        from sqlalchemy import select

        from structure.extensions.database import get_session
        from structure.models.runs.task import Task

        try:
            async with get_session("structure") as db:
                stmt = (
                    select(
                        Task.id,
                        Task.title,
                        Task.status,
                        Task.priority,
                        Task.assignee,
                        Task.run_id,
                        Task.parent_task_id,
                        Task.created_at,
                        Task.completed_at,
                    )
                    .where(Task.workspace_id == UUID(input_data.workspace_id))
                    .order_by(Task.priority.desc(), Task.created_at.asc())
                    .limit(input_data.limit)
                )

                if input_data.run_id:
                    stmt = stmt.where(Task.run_id == UUID(input_data.run_id))

                if input_data.status:
                    stmt = stmt.where(Task.status == input_data.status)

                if input_data.parent_task_id:
                    stmt = stmt.where(Task.parent_task_id == UUID(input_data.parent_task_id))

                result = await db.execute(stmt)
                rows = result.all()

            tasks = [
                {
                    "task_id": str(row.id),
                    "title": row.title,
                    "status": row.status,
                    "priority": row.priority,
                    "assignee": row.assignee,
                    "run_id": str(row.run_id) if row.run_id else None,
                    "parent_task_id": str(row.parent_task_id) if row.parent_task_id else None,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                }
                for row in rows
            ]

            return ToolOutputSchema(
                success=True,
                message=f"Found {len(tasks)} task(s)",
                data={"tasks": tasks, "count": len(tasks)},
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to list tasks: {e!s}",
                error=str(e),
            )
