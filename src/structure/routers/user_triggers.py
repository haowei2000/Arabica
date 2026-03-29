# structure/routers/user_triggers.py
"""REST API endpoints for user-level trigger template management."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.dependencies.auth import get_current_user
from structure.extensions.database import get_aiwen_db
from structure.models.workspaces.workspace_trigger import WorkspaceTrigger
from structure.schemas.auth.user import UserResponse
from structure.schemas.workspaces.trigger import (
    TriggerCreate,
    TriggerListResponse,
    TriggerResponse,
    TriggerUpdate,
)
from structure.services.context.context_syncer import ContextSyncer

router = APIRouter(prefix="/triggers", tags=["triggers"])


def _parse_uuid(value: object, field: str = "id") -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid {field}: {value}",
        )


@router.post("", response_model=TriggerResponse, status_code=status.HTTP_201_CREATED)
async def create_user_trigger(
    data: TriggerCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """Create a user-level trigger template (workspace_id=null)."""
    user_uuid = _parse_uuid(current_user.id, "user_id")
    trigger = WorkspaceTrigger(
        workspace_id=None,
        user_id=user_uuid,
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
        created_by=user_uuid,
    )
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)
    await ContextSyncer(db).sync_trigger(trigger, current_user.id)
    return TriggerResponse.model_validate(trigger)


@router.get("", response_model=TriggerListResponse)
async def list_user_triggers(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
    event_type: str | None = Query(None, description="Filter by event type"),
    enabled: bool | None = Query(None, description="Filter by enabled state"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """List user-level trigger templates (workspace_id IS NULL)."""
    user_uuid = _parse_uuid(current_user.id, "user_id")
    conditions = [
        WorkspaceTrigger.user_id == user_uuid,
        WorkspaceTrigger.workspace_id.is_(None),
    ]
    if event_type is not None:
        conditions.append(WorkspaceTrigger.event_type == event_type)
    if enabled is not None:
        conditions.append(WorkspaceTrigger.enabled == enabled)

    count_stmt = select(func.count()).select_from(WorkspaceTrigger).where(*conditions)
    total = (await db.execute(count_stmt)).scalar_one()

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
async def get_user_trigger(
    trigger_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """Get a single user-level trigger template by ID."""
    user_uuid = _parse_uuid(current_user.id, "user_id")
    t_uuid = _parse_uuid(trigger_id, "trigger_id")
    stmt = select(WorkspaceTrigger).where(
        WorkspaceTrigger.id == t_uuid,
        WorkspaceTrigger.user_id == user_uuid,
        WorkspaceTrigger.workspace_id.is_(None),
    )
    trigger = (await db.execute(stmt)).scalar_one_or_none()
    if not trigger:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trigger {trigger_id} not found",
        )
    return TriggerResponse.model_validate(trigger)


@router.put("/{trigger_id}", response_model=TriggerResponse)
async def update_user_trigger(
    trigger_id: str,
    data: TriggerUpdate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """Update a user-level trigger template."""
    user_uuid = _parse_uuid(current_user.id, "user_id")
    t_uuid = _parse_uuid(trigger_id, "trigger_id")
    stmt = select(WorkspaceTrigger).where(
        WorkspaceTrigger.id == t_uuid,
        WorkspaceTrigger.user_id == user_uuid,
        WorkspaceTrigger.workspace_id.is_(None),
    )
    trigger = (await db.execute(stmt)).scalar_one_or_none()
    if not trigger:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trigger {trigger_id} not found",
        )
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(trigger, field, value)
    await db.commit()
    await db.refresh(trigger)
    await ContextSyncer(db).sync_trigger(trigger, current_user.id)
    return TriggerResponse.model_validate(trigger)


@router.delete("/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_trigger(
    trigger_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """Delete a user-level trigger template."""
    user_uuid = _parse_uuid(current_user.id, "user_id")
    t_uuid = _parse_uuid(trigger_id, "trigger_id")
    stmt = select(WorkspaceTrigger).where(
        WorkspaceTrigger.id == t_uuid,
        WorkspaceTrigger.user_id == user_uuid,
        WorkspaceTrigger.workspace_id.is_(None),
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
