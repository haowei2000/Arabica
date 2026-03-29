"""Delete task tool - remove a task from the workspace."""

from pydantic import Field

from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class DeleteTaskTool(InnerTool):
    """Delete a task by its ID."""

    METADATA = ToolMetadata(
        name="delete_task",
        display_name="Delete Task",
        description="Permanently delete a task by its ID. Subtasks will have their parent_task_id set to NULL.",
        category="task",
        tags=["task", "delete", "remove"],
        timeout=10,
    )

    class InputSchema(ToolInputSchema):
        task_id: str = Field(description="Task ID to delete")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID

        from sqlalchemy import delete

        from structure.extensions.database import get_session
        from structure.models.runs.task import Task

        try:
            async with get_session("structure") as db:
                stmt = delete(Task).where(Task.id == UUID(input_data.task_id))
                result = await db.execute(stmt)
                await db.commit()
                deleted = result.rowcount

            if deleted == 0:
                return ToolOutputSchema(
                    success=False,
                    message=f"Task not found: {input_data.task_id}",
                    data={"task_id": input_data.task_id, "exists": False},
                )

            return ToolOutputSchema(
                success=True,
                message=f"Deleted task: {input_data.task_id}",
                data={"task_id": input_data.task_id},
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to delete task: {e!s}",
                error=str(e),
                data={"task_id": input_data.task_id},
            )
