# aiwen/routers/workspaces/runs.py
"""REST API endpoints for run management."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from aiwen.config.factory import get_settings
from aiwen.core.dependencies.auth import get_current_user
from aiwen.core.dependencies.workspace import (
    EventPublisherDep,
    RunCRUDDep,
    RunStateMachineDep,
    WorkspaceCRUDDep,
)
from aiwen.core.enums.runs import TriggerType
from aiwen.models.app import App
from aiwen.models.executor.executor import ExecutorTemplate
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.events.event_payloads import EventType, UserMessageEventSchema
from aiwen.schemas.runs.run import (
    RunFeedbackRequest,
    RunListResponse,
    RunResponse,
    RunResumeRequest,
    RunStatus,
)

_redis_cfg = get_settings().redis
REDIS_RUN_LABEL = _redis_cfg.run_label
REDIS_RUN_RESUME_APPROVAL_SUFFIX = _redis_cfg.run_resume_approval_suffix

router = APIRouter(prefix="/workspaces/{workspace_id}/runs", tags=["runs"])


@router.post("", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
async def create_run(
    user_message_event: UserMessageEventSchema,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    workspace_crud: WorkspaceCRUDDep,
    run_crud: RunCRUDDep,
    event_publisher: EventPublisherDep,
    state_machine: RunStateMachineDep,
):
    """
    Create and start a new run with a user message.

    Args:
        state_machine:
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
    workspace_id = user_message_event.workspace_id
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    # Determine app_id (from request or workspace default)
    app_id = user_message_event.app_id

    # Create run (app_id is now optional)
    run = await run_crud.create(
        workspace_id=workspace_id,
        app_id=app_id,
        user_id=current_user.id,
        trigger_type=TriggerType.USER,
        auto_commit=False,
    )

    # Resolve executor_code:
    #   1. app.executor_id → ExecutorTemplate.executor_code  (legacy)
    #   2. workspace.executor_code  (new native config)
    #   3. default "SimpleAgent"
    executor_code = "SimpleAgent"
    if app_id:
        app_result = await state_machine.db.execute(select(App).where(App.id == app_id))
        app_row = app_result.scalar_one_or_none()
        if app_row and app_row.executor_id:
            tmpl_result = await state_machine.db.execute(
                select(ExecutorTemplate).where(ExecutorTemplate.id == app_row.executor_id)
            )
            tmpl = tmpl_result.scalar_one_or_none()
            if tmpl:
                executor_code = tmpl.executor_code
    else:
        # Fall back to workspace's own executor_code
        if workspace.executor_code:
            executor_code = workspace.executor_code

    # Publish user message event (also triggers Worker via run_tasks stream)
    await event_publisher.publish(
        event_type=EventType.USER_MESSAGE,
        workspace_id=workspace_id,
        run_id=str(run.id),
        app_id=str(app_id) if app_id else None,
        user_id=str(current_user.id),
        executor_code=executor_code,
        payload={
            "message": user_message_event.payload.message,
        },
        auto_commit=True,
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
    status_filter: str | None = Query(
        None, alias="status", description="Filter by status"
    ),
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
    executor_code = waiting_info.get("executor_code", "SimpleAgent")

    # ── store approval for the worker ──────────────────────────
    if state_machine.redis:
        await state_machine.redis.set(
            f"{REDIS_RUN_LABEL}:{run_id}:{REDIS_RUN_RESUME_APPROVAL_SUFFIX}",
            json.dumps(
                {
                    "approval": data.approval,
                    "tool_result": data.tool_result,
                    "user_input": data.user_input,
                }
            ),
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
        from aiwen.services.events.event_worker import RUN_STREAM

        await state_machine.redis.xadd(
            RUN_STREAM,
            fields={
                "run_id": run_id,
                "executor_code": executor_code,
                "input": json.dumps(run.input_data or {}),
                "triggered_by": "resume",
            },
        )

    return run


@router.post("/{run_id}/feedback", response_model=RunResponse)
async def submit_feedback(
    workspace_id: str,
    run_id: str,
    data: RunFeedbackRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    workspace_crud: WorkspaceCRUDDep,
    run_crud: RunCRUDDep,
    state_machine: RunStateMachineDep,
    event_publisher: EventPublisherDep,
):
    """Submit user feedback in response to an agent.query event.

    Used when the agent called ``ask_for_user`` and the run is waiting for
    the user's answer.  This endpoint:

    1. Transitions the run from ``waiting`` back to ``running``.
    2. Publishes a ``user.feedback`` event carrying the user's text so the
       executor can inject it as the tool result and resume the loop.
    """
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

    waiting_info = run.waiting_for or {}
    if waiting_info.get("type") != "user_input":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Run is not waiting for user input",
        )

    # Transition: waiting → running
    try:
        run = await state_machine.resume_from_tool(run_id=run_id, auto_commit=True)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Publish user.feedback — the executor stream consumer will pick this up
    # and forward it to the executor's _on_user_feedback handler.
    await event_publisher.publish(
        event_type=EventType.USER_FEEDBACK,
        workspace_id=workspace_id,
        run_id=run_id,
        user_id=str(current_user.id),
        payload={
            "feedback": data.feedback,
            "tool_name": waiting_info.get("tool_name", "ask_for_user"),
            "tool_id": waiting_info.get("tool_id"),
        },
        auto_commit=True,
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
