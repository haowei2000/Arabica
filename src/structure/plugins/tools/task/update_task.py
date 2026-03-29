"""Update task tool - modify task status, description, or metadata."""

from typing import Any

from pydantic import Field

from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class UpdateTaskTool(InnerTool):
    """Update a task's status, title, description, or metadata."""

    METADATA = ToolMetadata(
        name="update_task",
        display_name="Update Task",
        description=(
            "Update a task's fields: status, title, description, result, priority, or metadata. "
            "Set status to 'done' to complete a task (automatically records completion time)."
        ),
        category="task",
        tags=["task", "update", "status", "progress"],
        timeout=15,
    )

    class InputSchema(ToolInputSchema):
        task_id: str = Field(description="Task ID to update")
        status: str | None = Field(
            default=None,
            description="New status: pending, in_progress, done, failed, cancelled",
        )
        title: str | None = Field(default=None, description="New task title")
        description: str | None = Field(default=None, description="New task description")
        result: str | None = Field(
            default=None, description="Task result or output (set when completing)"
        )
        priority: int | None = Field(
            default=None, description="New priority (1-5)", ge=1, le=5
        )
        assignee: str | None = Field(default=None, description="New assignee")
        meta: dict[str, Any] | None = Field(
            default=None, description="Metadata to merge into existing metadata"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from datetime import UTC, datetime
        from uuid import UUID

        from sqlalchemy import select

        from structure.extensions.database import get_session
        from structure.models.runs.task import Task

        try:
            async with get_session("structure") as db:
                stmt = select(Task).where(Task.id == UUID(input_data.task_id))
                result = await db.execute(stmt)
                task = result.scalar_one_or_none()

                if task is None:
                    return ToolOutputSchema(
                        success=False,
                        message=f"Task not found: {input_data.task_id}",
                        data={"task_id": input_data.task_id, "exists": False},
                    )

                updated_fields = []

                if input_data.status is not None:
                    task.status = input_data.status
                    updated_fields.append("status")
                    if input_data.status == "done" and task.completed_at is None:
                        task.completed_at = datetime.now(UTC)

                if input_data.title is not None:
                    task.title = input_data.title
                    updated_fields.append("title")

                if input_data.description is not None:
                    task.description = input_data.description
                    updated_fields.append("description")

                if input_data.result is not None:
                    task.result = input_data.result
                    updated_fields.append("result")

                if input_data.priority is not None:
                    task.priority = input_data.priority
                    updated_fields.append("priority")

                if input_data.assignee is not None:
                    task.assignee = input_data.assignee
                    updated_fields.append("assignee")

                if input_data.meta is not None:
                    task.meta = {**(task.meta or {}), **input_data.meta}
                    updated_fields.append("meta")

                if not updated_fields:
                    return ToolOutputSchema(
                        success=False,
                        message="No fields provided to update",
                        data={"task_id": input_data.task_id},
                    )

                await db.commit()

            return ToolOutputSchema(
                success=True,
                message=f"Updated task '{task.title}' — status: {task.status}",
                data={
                    "task_id": input_data.task_id,
                    "updated_fields": updated_fields,
                    "status": task.status,
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to update task: {e!s}",
                error=str(e),
                data={"task_id": input_data.task_id},
            )
