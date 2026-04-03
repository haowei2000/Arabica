"""Create task tool - add a new task to the agent loop."""

from typing import Any

from pydantic import Field

from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class CreateTaskTool(InnerTool):
    """Create a new task to track a unit of work in the agent loop."""

    METADATA = ToolMetadata(
        name="create_task",
        display_name="Create Task",
        description=(
            "Create a new task to track a unit of work. Tasks can be nested via parent_task_id "
            "to form hierarchical plans. Returns the task ID for future updates."
        ),
        category="task",
        tags=["task", "create", "plan", "todo"],
        timeout=15,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        title: str = Field(description="Task title — short, actionable description")
        description: str | None = Field(
            default=None, description="Detailed task description or acceptance criteria"
        )
        run_id: str | None = Field(
            default=None, description="Run ID this task belongs to"
        )
        parent_task_id: str | None = Field(
            default=None, description="Parent task ID for subtasks"
        )
        priority: int = Field(
            default=2,
            description="Priority: 1=low, 2=normal, 3=high, 4=urgent, 5=critical",
            ge=1,
            le=5,
        )
        assignee: str | None = Field(
            default=None,
            description="Who this task is assigned to (e.g., agent name, tool name)",
        )
        meta: dict[str, Any] | None = Field(
            default=None, description="Additional metadata"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID, uuid4

        from structure.extensions.database import get_session
        from structure.models.runs.task import Task

        try:
            task = Task(
                id=uuid4(),
                workspace_id=UUID(input_data.workspace_id),
                run_id=UUID(input_data.run_id) if input_data.run_id else None,
                parent_task_id=UUID(input_data.parent_task_id)
                if input_data.parent_task_id
                else None,
                title=input_data.title,
                description=input_data.description,
                priority=input_data.priority,
                assignee=input_data.assignee,
                meta=input_data.meta or {},
            )

            async with get_session("structure") as db:
                db.add(task)
                await db.commit()
                task_id = str(task.id)

            return ToolOutputSchema(
                success=True,
                message=f"Created task '{input_data.title}'",
                data={
                    "task_id": task_id,
                    "title": input_data.title,
                    "status": "pending",
                    "priority": input_data.priority,
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to create task: {e!s}",
                error=str(e),
            )
