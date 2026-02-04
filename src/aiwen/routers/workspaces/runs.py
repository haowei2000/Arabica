# aiwen/routers/workspaces/runs.py
"""REST API endpoints for run management."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from aiwen.dependencies.auth import get_current_user
from aiwen.dependencies.workspace import (
    EventPublisherDep,
    RunCRUDDep,
    RunStateMachineDep,
    WorkspaceCRUDDep,
)
from aiwen.models.agents.agent_template import AgentTemplate
from aiwen.models.agents.app import App
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.events.event_payloads import EventType
from aiwen.schemas.runs.run import (
    RunListResponse,
    RunResponse,
    RunResumeRequest,
    RunStartRequest,
    RunStatus,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/runs", tags=["runs"])


@router.post(
    "", response_model=RunResponse, status_code=status.HTTP_201_CREATED
)
async def create_run(
        workspace_id: str,
        data: RunStartRequest,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        workspace_crud: WorkspaceCRUDDep,
        run_crud: RunCRUDDep,
        event_publisher: EventPublisherDep,
        state_machine: RunStateMachineDep,
):
    """
    Create and start a new run with a user message.

    Args:
        workspace_id: The workspace ID
        data: Run start request with message
        current_user: Current authenticated user
        workspace_crud: Workspace CRUD
        run_crud: Run CRUD
        event_publisher: Event publisher

    Returns:
        Created run
    """
    # Verify workspace access
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    # Determine app_id (from request or workspace default)
    app_id = data.app_id or workspace.app_id
    if not app_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No app_id provided and workspace has no default app",
        )

    # Create run
    run = await run_crud.create(
        workspace_id=workspace_id,
        app_id=app_id,
        user_id=current_user.id,
        trigger_type="user",
        input_data={
            "message": data.message,
            "attachments": data.attachments,
            "metadata": data.metadata,
        },
        auto_commit=False,
    )

    # Publish user message event
    await event_publisher.publish(
        event_type=EventType.USER_MESSAGE,
        workspace_id=workspace_id,
        run_id=str(run.id),
        user_id=str(current_user.id),
        payload={
            "content": data.message,
            "attachments": data.attachments,
        },
        auto_commit=True,
    )

    # ── trigger the worker ──────────────────────────────────
    # Resolve executor_code from the app's linked agent template so the
    # worker knows which Executor class to instantiate.
    if state_machine.redis:
        executor_code = "DEFAULT001"  # safe fallback
        app_result = await state_machine.db.execute(
            select(App).where(App.id == app_id)
        )
        app_row = app_result.scalar_one_or_none()
        if app_row and app_row.agent_template_id:
            tmpl_result = await state_machine.db.execute(
                select(AgentTemplate).where(
                    AgentTemplate.id == app_row.agent_template_id
                )
            )
            tmpl = tmpl_result.scalar_one_or_none()
            if tmpl:
                executor_code = tmpl.template_code

        from aiwen.workers.event_worker import AGENT_WORKER_STREAM

        await state_machine.redis.xadd(
            AGENT_WORKER_STREAM,
            fields={
                "run_id": str(run.id),
                "executor_code": executor_code,
                "input": json.dumps(run.input_data or {}),
                "triggered_by": "user",
            },
        )

    return run


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
        workspace_id: str,
        run_id: str,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        workspace_crud: WorkspaceCRUDDep,
        run_crud: RunCRUDDep,
):
    """
    Get run by ID.

    Args:
        workspace_id: The workspace ID
        run_id: The run ID
        current_user: Current authenticated user

    Returns:
        Run details
    """
    # Verify workspace access
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    run = await run_crud.get_by_id(run_id)
    if not run or str(run.workspace_id) != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    return run


@router.get("", response_model=RunListResponse)
async def list_runs(
        workspace_id: str,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        workspace_crud: WorkspaceCRUDDep,
        run_crud: RunCRUDDep,
        status_filter: str | None = Query(None, alias="status", description="Filter by status"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
):
    """
    List runs in a workspace.

    Args:
        workspace_id: The workspace ID
        current_user: Current authenticated user
        status_filter: Filter by run status
        page: Page number
        page_size: Items per page

    Returns:
        Paginated list of runs
    """
    # Verify workspace access
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    skip = (page - 1) * page_size
    items, total = await run_crud.list_by_workspace(
        workspace_id=workspace_id,
        skip=skip,
        limit=page_size,
        status=status_filter,
    )

    return RunListResponse(
        total=total,
        items=items,
        page=page,
        page_size=page_size,
    )


@router.post("/{run_id}/start", response_model=RunResponse)
async def start_run(
        workspace_id: str,
        run_id: str,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        workspace_crud: WorkspaceCRUDDep,
        run_crud: RunCRUDDep,
        state_machine: RunStateMachineDep,
):
    """
    Start a pending run.

    Args:
        workspace_id: The workspace ID
        run_id: The run ID
        current_user: Current authenticated user

    Returns:
        Updated run
    """
    # Verify workspace access
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    run = await run_crud.get_by_id(run_id)
    if not run or str(run.workspace_id) != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    try:
        run = await state_machine.start(
            run_id=run_id,
            triggered_by=f"user:{current_user.id}",
            auto_commit=True,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return run


@router.post("/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
        workspace_id: str,
        run_id: str,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        workspace_crud: WorkspaceCRUDDep,
        run_crud: RunCRUDDep,
        state_machine: RunStateMachineDep,
        reason: str = Query("Cancelled by user", description="Cancellation reason"),
):
    """
    Cancel a running or waiting run.

    Args:
        workspace_id: The workspace ID
        run_id: The run ID
        current_user: Current authenticated user
        reason: Cancellation reason

    Returns:
        Updated run
    """
    # Verify workspace access
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    run = await run_crud.get_by_id(run_id)
    if not run or str(run.workspace_id) != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    try:
        run = await state_machine.cancel(
            run_id=run_id,
            reason=reason,
            triggered_by=f"user:{current_user.id}",
            auto_commit=True,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return run


@router.post("/{run_id}/resume", response_model=RunResponse)
async def resume_run(
        workspace_id: str,
        run_id: str,
        data: RunResumeRequest,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        workspace_crud: WorkspaceCRUDDep,
        run_crud: RunCRUDDep,
        state_machine: RunStateMachineDep,
        event_publisher: EventPublisherDep,
):
    """
    Resume a waiting run (after tool approval or result).

    Args:
        workspace_id: The workspace ID
        run_id: The run ID
        data: Resume data (tool result, user input, or approval)
        current_user: Current authenticated user

    Returns:
        Updated run
    """
    # Verify workspace access
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    run = await run_crud.get_by_id(run_id)
    if not run or str(run.workspace_id) != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    if run.status != RunStatus.WAITING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Run is not in waiting state (current: {run.status})",
        )

    # Read waiting_for BEFORE resume_from_tool clears it – we need
    # executor_code to re-trigger the worker on the correct stream.
    waiting_info = run.waiting_for or {}
    executor_code = waiting_info.get("executor_code", "DEFAULT001")

    # ── store approval for the worker ──────────────────────────
    if state_machine.redis:
        await state_machine.redis.set(
            f"run:{run_id}:resume_approval",
            json.dumps({
                "approval": data.approval,
                "tool_result": data.tool_result,
                "user_input": data.user_input,
            }),
            ex=300,  # 5-minute TTL – worker consumes almost instantly
        )

    # Publish tool-result event into the run's event log
    await event_publisher.publish(
        event_type=EventType.TOOL_RESULT,
        workspace_id=workspace_id,
        run_id=run_id,
        user_id=str(current_user.id),
        payload={
            "tool_name": waiting_info.get("tool_name", "unknown"),
            "tool_id": waiting_info.get("tool_id", "unknown"),
            "result": data.tool_result,
            "success": data.approval if data.approval is not None else True,
        },
        auto_commit=False,
    )

    # Resume the run (waiting → running)
    try:
        run = await state_machine.resume_from_tool(
            run_id=run_id,
            auto_commit=True,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # ── re-trigger the worker ──────────────────────────────────
    # Push onto the same Redis stream the worker polls so it picks
    # up the resumed run and calls stream() with the approval data.
    if state_machine.redis:
        from aiwen.workers.event_worker import AGENT_WORKER_STREAM

        await state_machine.redis.xadd(
            AGENT_WORKER_STREAM,
            fields={
                "run_id": run_id,
                "executor_code": executor_code,
                "input": json.dumps(run.input_data or {}),
                "triggered_by": "resume",
            },
        )

    return run


# Standalone runs router (not nested under workspace)
runs_standalone_router = APIRouter(prefix="/runs", tags=["runs"])


@runs_standalone_router.get("/{run_id}", response_model=RunResponse)
async def get_run_by_id(
        run_id: str,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        run_crud: RunCRUDDep,
):
    """
    Get run by ID (standalone endpoint).

    Args:
        run_id: The run ID
        current_user: Current authenticated user

    Returns:
        Run details
    """
    run = await run_crud.get_by_id_and_user(run_id, current_user.id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found or access denied",
        )
    return run


@runs_standalone_router.get("", response_model=RunListResponse)
async def list_user_runs(
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        run_crud: RunCRUDDep,
        status_filter: str | None = Query(None, alias="status"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
):
    """
    List all runs for current user.

    Args:
        current_user: Current authenticated user
        status_filter: Filter by status
        page: Page number
        page_size: Items per page

    Returns:
        Paginated list of runs
    """
    skip = (page - 1) * page_size
    items, total = await run_crud.list_by_user(
        user_id=current_user.id,
        skip=skip,
        limit=page_size,
        status=status_filter,
    )

    return RunListResponse(
        total=total,
        items=items,
        page=page,
        page_size=page_size,
    )
