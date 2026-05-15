# structure/routers/triggers.py
"""REST API endpoints for workspace trigger management."""

from types import SimpleNamespace
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.dependencies.auth import get_current_user
from structure.extensions.database import get_structure_db
from structure.models.workspaces.workspace_trigger import WorkspaceTrigger
from structure.schemas.auth.user import UserResponse
from structure.schemas.workspaces.trigger import (
    TriggerCreate,
    TriggerListResponse,
    TriggerResponse,
    TriggerTestRequest,
    TriggerTestResponse,
    TriggerUpdate,
)
from structure.services.context.context_syncer import ContextSyncer

router = APIRouter(prefix="/workspaces/{workspace_id}/triggers", tags=["triggers"])


def _workspace_uuid(workspace_id: str) -> UUID:
    """Parse and validate workspace_id path parameter."""
    try:
        return UUID(workspace_id)
    except ValueError:
        raise HTTPException(  # noqa: B904
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid workspace_id: {workspace_id}",
        )


@router.post("", response_model=TriggerResponse, status_code=status.HTTP_201_CREATED)
async def create_trigger(
    workspace_id: str,
    data: TriggerCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_structure_db)],
):
    """Create a new trigger for a workspace.

    Args:
        workspace_id: Workspace UUID string.
        data: Trigger creation payload.
        current_user: Authenticated user.
        db: Database session.

    Returns:
        The created trigger.
    """
    ws_uuid = _workspace_uuid(workspace_id)

    trigger = WorkspaceTrigger(
        workspace_id=ws_uuid,
        name=data.name,
        description=data.description,
        event_type=data.event_type,
        condition_type=data.condition_type,
        condition_value=data.condition_value,
        condition_field=data.condition_field,
        tool_name=data.tool_name,
        action_params=data.action_params,
        priority=data.priority,
        enabled=data.enabled,
        created_by=UUID(str(current_user.id)) if current_user.id else None,
    )
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)
    await ContextSyncer(db).sync_trigger(trigger, current_user.id)
    return TriggerResponse.model_validate(trigger)


@router.get("", response_model=TriggerListResponse)
async def list_triggers(
    workspace_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],  # noqa: ARG001
    db: Annotated[AsyncSession, Depends(get_structure_db)],
    event_type: str | None = Query(None, description="Filter by event type"),
    enabled: bool | None = Query(None, description="Filter by enabled state"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """List workspace triggers with optional filters.

    Args:
        workspace_id: Workspace UUID string.
        event_type: Optional filter by event type.
        enabled: Optional filter by enabled state.
        page: Page number (1-based).
        page_size: Number of items per page.

    Returns:
        Paginated list of triggers.
    """
    ws_uuid = _workspace_uuid(workspace_id)

    conditions = [WorkspaceTrigger.workspace_id == ws_uuid]
    if event_type is not None:
        conditions.append(WorkspaceTrigger.event_type == event_type)
    if enabled is not None:
        conditions.append(WorkspaceTrigger.enabled == enabled)

    # Count total
    count_stmt = select(func.count()).select_from(WorkspaceTrigger).where(*conditions)
    total = (await db.execute(count_stmt)).scalar_one()

    # Fetch page
    stmt = (
        select(WorkspaceTrigger)
        .where(*conditions)
        .order_by(WorkspaceTrigger.priority, WorkspaceTrigger.created_at)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    items = [TriggerResponse.model_validate(t) for t in result.scalars().all()]

    return TriggerListResponse(total=total, items=items, page=page, page_size=page_size)


@router.get("/{trigger_id}", response_model=TriggerResponse)
async def get_trigger(
    workspace_id: str,
    trigger_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],  # noqa: ARG001
    db: Annotated[AsyncSession, Depends(get_structure_db)],
):
    """Get a single trigger by ID.

    Args:
        workspace_id: Workspace UUID string.
        trigger_id: Trigger UUID string.

    Returns:
        The trigger.

    Raises:
        HTTPException 404: If trigger not found.
    """
    ws_uuid = _workspace_uuid(workspace_id)
    t_uuid = _workspace_uuid(trigger_id)

    stmt = select(WorkspaceTrigger).where(
        WorkspaceTrigger.id == t_uuid,
        WorkspaceTrigger.workspace_id == ws_uuid,
    )
    trigger = (await db.execute(stmt)).scalar_one_or_none()
    if not trigger:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trigger {trigger_id} not found",
        )
    return TriggerResponse.model_validate(trigger)


@router.put("/{trigger_id}", response_model=TriggerResponse)
async def update_trigger(
    workspace_id: str,
    trigger_id: str,
    data: TriggerUpdate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_structure_db)],
):
    """Update an existing trigger.

    Args:
        workspace_id: Workspace UUID string.
        trigger_id: Trigger UUID string.
        data: Fields to update (only provided fields are changed).

    Returns:
        The updated trigger.

    Raises:
        HTTPException 404: If trigger not found.
    """
    ws_uuid = _workspace_uuid(workspace_id)
    t_uuid = _workspace_uuid(trigger_id)

    stmt = select(WorkspaceTrigger).where(
        WorkspaceTrigger.id == t_uuid,
        WorkspaceTrigger.workspace_id == ws_uuid,
    )
    trigger = (await db.execute(stmt)).scalar_one_or_none()
    if not trigger:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trigger {trigger_id} not found",
        )

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(trigger, field, value)

    await db.commit()
    await db.refresh(trigger)
    await ContextSyncer(db).sync_trigger(trigger, current_user.id)
    return TriggerResponse.model_validate(trigger)


@router.delete("/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trigger(
    workspace_id: str,
    trigger_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_structure_db)],
):
    """Delete a trigger.

    Args:
        workspace_id: Workspace UUID string.
        trigger_id: Trigger UUID string.

    Raises:
        HTTPException 404: If trigger not found.
    """
    ws_uuid = _workspace_uuid(workspace_id)
    t_uuid = _workspace_uuid(trigger_id)

    stmt = select(WorkspaceTrigger).where(
        WorkspaceTrigger.id == t_uuid,
        WorkspaceTrigger.workspace_id == ws_uuid,
    )
    trigger = (await db.execute(stmt)).scalar_one_or_none()
    if not trigger:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trigger {trigger_id} not found",
        )

    await ContextSyncer(db).remove_trigger(trigger, current_user.id)
    await db.delete(trigger)
    await db.commit()


@router.post("/{trigger_id}/test", response_model=TriggerTestResponse)
async def test_trigger(
    workspace_id: str,
    trigger_id: str,
    data: TriggerTestRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_structure_db)],
):
    """Dry-run test a trigger against a sample payload.

    Evaluates the trigger condition and, if matched, executes the action.
    No side effects on runs or events.

    Args:
        workspace_id: Workspace UUID string.
        trigger_id: Trigger UUID string.
        data: Sample payload to test against.

    Returns:
        Whether the trigger matched and the action result if it did.
    """
    from structure.services.triggers.trigger_processor import TriggerConditionEvaluator

    ws_uuid = _workspace_uuid(workspace_id)
    t_uuid = _workspace_uuid(trigger_id)

    stmt = select(WorkspaceTrigger).where(
        WorkspaceTrigger.id == t_uuid,
        WorkspaceTrigger.workspace_id == ws_uuid,
    )
    trigger = (await db.execute(stmt)).scalar_one_or_none()
    if not trigger:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trigger {trigger_id} not found",
        )

    evaluator = TriggerConditionEvaluator()
    matched = evaluator.evaluate(trigger, data.payload)

    result: Any = None
    error: str | None = None

    if matched:
        from structure.services.triggers.trigger_processor import TriggerProcessor

        proc = TriggerProcessor(db, workspace_id)
        try:
            event = SimpleNamespace(
                event_type=trigger.event_type,
                payload=data.payload,
                user_id=current_user.id,
            )
            result = await proc._execute_action(trigger, event)
        except Exception as exc:
            error = str(exc)
            matched = False

    return TriggerTestResponse(
        matched=matched,
        trigger_id=str(trigger.id),
        trigger_name=trigger.name,
        tool_name=trigger.tool_name,
        result=result,
        error=error,
    )
