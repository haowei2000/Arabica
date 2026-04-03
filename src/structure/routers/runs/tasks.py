"""REST API endpoints for task management."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.dependencies.auth import get_current_user
from structure.core.dependencies.workspace import WorkspaceCRUDDep
from structure.extensions.database import get_structure_db
from structure.schemas.auth.user import UserResponse
from structure.schemas.runs.task import TaskListResponse, TaskResponse
from structure.services.runs.task_crud import TaskCRUD

router = APIRouter(prefix="/workspaces/{workspace_id}/tasks", tags=["tasks"])


async def get_task_crud(
    db: Annotated[AsyncSession, Depends(get_structure_db)],
) -> TaskCRUD:
    return TaskCRUD(db)


TaskCRUDDep = Annotated[TaskCRUD, Depends(get_task_crud)]


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    workspace_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    workspace_crud: WorkspaceCRUDDep,
    task_crud: TaskCRUDDep,
    run_id: str | None = Query(None, description="Filter by run ID"),
    task_status: str | None = Query(
        None, alias="status", description="Filter by status"
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List tasks in a workspace."""
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    total, items = await task_crud.list_by_workspace(
        workspace_id=workspace_id,
        run_id=run_id,
        status=task_status,
        limit=limit,
        offset=offset,
    )
    return TaskListResponse(
        total=total,
        items=[TaskResponse.model_validate(t) for t in items],
    )
