"""REST API endpoints for Event CRUD and search operations."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from structure.core.dependencies.auth import get_current_user
from structure.core.dependencies.workspace import (
    EventCRUDDep,
    RunCRUDDep,
    WorkspaceCRUDDep,
)
from structure.schemas.auth.user import UserResponse
from structure.schemas.events.event_payloads import EventListResponse, EventResponse

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(
    event_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    event_crud: EventCRUDDep,
    workspace_crud: WorkspaceCRUDDep,
):
    """Get a single event by ID.

    Args:
        event_id: The event UUID
        current_user: Current authenticated user
        event_crud: Event CRUD dependency
        workspace_crud: Workspace CRUD for access check

    Raises:
        HTTPException 404: If event not found or no access
    """
    event = await event_crud.get_by_id(event_id)
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event '{event_id}' not found",
        )

    # Verify user has access to the event's workspace
    workspace = await workspace_crud.get_by_id_and_user(
        event.workspace_id, current_user.id
    )
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this event's workspace",
        )

    return event


@router.get("/workspace/{workspace_id}/list", response_model=EventListResponse)
async def list_events_by_workspace(
    workspace_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    event_crud: EventCRUDDep,
    workspace_crud: WorkspaceCRUDDep,
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
    event_types: str | None = Query(
        None, description="Comma-separated event types to filter"
    ),
):
    """List events for a workspace with pagination.

    Args:
        workspace_id: The workspace UUID
        skip: Pagination offset
        limit: Maximum number of events
        event_types: Comma-separated event type filter
    """
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace '{workspace_id}' not found or access denied",
        )

    types_list = event_types.split(",") if event_types else None
    items, total = await event_crud.list_by_workspace(
        workspace_id=workspace_id,
        skip=skip,
        limit=limit,
        event_types=types_list,
    )

    last_seq = items[0].sequence if items else None
    return EventListResponse(total=total, items=items, last_sequence=last_seq)


@router.get("/run/{run_id}/list", response_model=EventListResponse)
async def list_events_by_run(
    run_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    event_crud: EventCRUDDep,
    run_crud: RunCRUDDep,
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
    event_types: str | None = Query(
        None, description="Comma-separated event types to filter"
    ),
):
    """List events for a run with pagination.

    Args:
        run_id: The run UUID
        skip: Pagination offset
        limit: Maximum number of events
        event_types: Comma-separated event type filter
    """
    run = await run_crud.get_by_id_and_user(run_id, current_user.id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found or access denied",
        )

    types_list = event_types.split(",") if event_types else None
    items, total = await event_crud.list_by_run(
        run_id=run_id,
        skip=skip,
        limit=limit,
        event_types=types_list,
    )

    last_seq = items[-1].sequence if items else None
    return EventListResponse(total=total, items=items, last_sequence=last_seq)


@router.get("/user/list", response_model=EventListResponse)
async def list_events_by_user(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    event_crud: EventCRUDDep,
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
    event_types: str | None = Query(
        None, description="Comma-separated event types to filter"
    ),
    workspace_id: UUID | None = Query(
        None, description="Narrow to a specific workspace"
    ),
):
    """List events triggered by the current user.

    Args:
        skip: Pagination offset
        limit: Maximum number of events
        event_types: Comma-separated event type filter
        workspace_id: Optional workspace filter
    """
    types_list = event_types.split(",") if event_types else None
    items, total = await event_crud.list_by_user(
        user_id=current_user.id,
        skip=skip,
        limit=limit,
        event_types=types_list,
        workspace_id=workspace_id,
    )

    last_seq = items[0].sequence if items else None
    return EventListResponse(total=total, items=items, last_sequence=last_seq)


@router.get("/search", response_model=EventListResponse)
async def search_events(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    event_crud: EventCRUDDep,
    workspace_crud: WorkspaceCRUDDep,
    workspace_id: UUID | None = Query(None, description="Filter by workspace"),
    run_id: UUID | None = Query(None, description="Filter by run"),
    user_id: UUID | None = Query(None, description="Filter by user"),
    event_types: str | None = Query(None, description="Comma-separated event types"),
    from_sequence: int | None = Query(
        None, ge=0, description="Minimum sequence (inclusive)"
    ),
    to_sequence: int | None = Query(
        None, ge=0, description="Maximum sequence (inclusive)"
    ),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
):
    """Search events with multiple filters.

    At least one of workspace_id, run_id, or user_id must be provided
    to scope the query.

    Args:
        workspace_id: Filter by workspace (optional)
        run_id: Filter by run (optional)
        user_id: Filter by user (optional)
        event_types: Comma-separated event types (optional)
        from_sequence: Min sequence inclusive (optional)
        to_sequence: Max sequence inclusive (optional)
        skip: Pagination offset
        limit: Max events
    """
    # Require at least one scope filter
    if not workspace_id and not run_id and not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of workspace_id, run_id, or user_id is required",
        )

    # Verify workspace access if filtering by workspace
    if workspace_id:
        workspace = await workspace_crud.get_by_id_and_user(
            workspace_id, current_user.id
        )
        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workspace '{workspace_id}' not found or access denied",
            )

    types_list = event_types.split(",") if event_types else None
    items, total = await event_crud.search(
        workspace_id=workspace_id,
        run_id=run_id,
        user_id=user_id,
        event_types=types_list,
        from_sequence=from_sequence,
        to_sequence=to_sequence,
        skip=skip,
        limit=limit,
    )

    last_seq = items[0].sequence if items else None
    return EventListResponse(total=total, items=items, last_sequence=last_seq)


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    event_crud: EventCRUDDep,
    workspace_crud: WorkspaceCRUDDep,
):
    """Delete an event by ID.

    Only the workspace owner can delete events.

    Args:
        event_id: The event UUID
        current_user: Current authenticated user

    Raises:
        HTTPException 404: If event not found
        HTTPException 403: If no permission
    """
    event = await event_crud.get_by_id(event_id)
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event '{event_id}' not found",
        )

    # Verify user has access and is workspace owner
    workspace = await workspace_crud.get_by_id_and_user(
        event.workspace_id, current_user.id
    )
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this event's workspace",
        )

    if str(workspace.owner_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the workspace owner can delete events",
        )

    await event_crud.delete(event_id)
    return
