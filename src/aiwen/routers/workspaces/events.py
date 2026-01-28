# aiwen/routers/workspaces/events.py
"""REST API endpoints for event streaming (SSE)."""

import asyncio
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from aiwen.dependencies.auth import get_current_user
from aiwen.dependencies.workspace import (
    EventConsumerDep,
    EventReplayerDep,
    RunCRUDDep,
    WorkspaceCRUDDep,
)
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.events.event_payloads import EventListResponse, EventResponse

router = APIRouter(tags=["events"])


async def _event_stream_generator(
        consumer: Any,
        subscribe_method: str,
        entity_id: str,
        last_id: str = "$",
):
    """Generate SSE events from Redis stream.

    Args:
        consumer: EventConsumer instance
        subscribe_method: Method name to call (subscribe_run or subscribe_workspace)
        entity_id: Run or workspace ID
        last_id: Last event ID for resumption
    """
    method = getattr(consumer, subscribe_method)

    try:
        async for event in method(entity_id, last_id=last_id):
            if event.get("type") == "keepalive":
                # Send keepalive comment
                yield f": keepalive {event.get('timestamp', '')}\n\n"
            elif event.get("type") == "error":
                # Send error event
                yield f"event: error\ndata: {json.dumps(event)}\n\n"
            else:
                # Send regular event
                event_type = event.get("event_type", "event")
                yield f"event: {event_type}\ndata: {json.dumps(event)}\n\n"

    except asyncio.CancelledError:
        # Client disconnected
        yield f"event: disconnect\ndata: {json.dumps({'reason': 'client_disconnected'})}\n\n"


@router.get("/runs/{run_id}/events/stream")
async def stream_run_events(
        run_id: str,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        run_crud: RunCRUDDep,
        consumer: EventConsumerDep,
        last_event_id: str = Query("$", description="Last event ID ($ for new events only)"),
):
    """
    Stream events for a run via Server-Sent Events (SSE).

    This endpoint provides real-time event streaming for a specific run.
    Connect to this endpoint to receive events as they occur.

    Args:
        run_id: The run ID
        current_user: Current authenticated user
        consumer: Event consumer
        last_event_id: Last event ID received (for resumption)

    Returns:
        SSE stream of events
    """
    # Verify run access
    run = await run_crud.get_by_id_and_user(run_id, current_user.id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found or access denied",
        )

    return StreamingResponse(
        _event_stream_generator(consumer, "subscribe_run", run_id, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/workspaces/{workspace_id}/events/stream")
async def stream_workspace_events(
        workspace_id: str,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        workspace_crud: WorkspaceCRUDDep,
        consumer: EventConsumerDep,
        last_event_id: str = Query("$", description="Last event ID"),
):
    """
    Stream events for a workspace via Server-Sent Events (SSE).

    This endpoint provides real-time event streaming for all runs in a workspace.

    Args:
        workspace_id: The workspace ID
        current_user: Current authenticated user
        consumer: Event consumer
        last_event_id: Last event ID received

    Returns:
        SSE stream of events
    """
    # Verify workspace access
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    return StreamingResponse(
        _event_stream_generator(consumer, "subscribe_workspace", workspace_id, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}/events", response_model=EventListResponse)
async def get_run_events(
        run_id: str,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        run_crud: RunCRUDDep,
        replayer: EventReplayerDep,
        from_sequence: int = Query(0, ge=0, description="Start sequence"),
        limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
):
    """
    Get historical events for a run.

    Use this endpoint to replay events or catch up after reconnection.

    Args:
        run_id: The run ID
        current_user: Current authenticated user
        replayer: Event replayer
        from_sequence: Start from this sequence number
        limit: Maximum number of events

    Returns:
        List of events
    """
    # Verify run access
    run = await run_crud.get_by_id_and_user(run_id, current_user.id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found or access denied",
        )

    events = await replayer.replay_run(
        run_id=run_id,
        from_sequence=from_sequence,
        limit=limit,
    )

    last_seq = events[-1].sequence if events else None

    return EventListResponse(
        total=len(events),
        items=events,
        last_sequence=last_seq,
    )


@router.get("/workspaces/{workspace_id}/events", response_model=EventListResponse)
async def get_workspace_events(
        workspace_id: str,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        workspace_crud: WorkspaceCRUDDep,
        replayer: EventReplayerDep,
        from_sequence: int = Query(0, ge=0, description="Start sequence"),
        limit: int = Query(100, ge=1, le=1000, description="Max events"),
        event_types: str | None = Query(None, description="Comma-separated event types"),
):
    """
    Get historical events for a workspace.

    Args:
        workspace_id: The workspace ID
        current_user: Current authenticated user
        replayer: Event replayer
        from_sequence: Start sequence
        limit: Maximum events
        event_types: Filter by event types

    Returns:
        List of events
    """
    # Verify workspace access
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    types_list = event_types.split(",") if event_types else None

    events = await replayer.replay_workspace(
        workspace_id=workspace_id,
        from_sequence=from_sequence,
        limit=limit,
        event_types=types_list,
    )

    last_seq = events[-1].sequence if events else None

    return EventListResponse(
        total=len(events),
        items=events,
        last_sequence=last_seq,
    )


@router.get("/runs/{run_id}/state")
async def get_run_state(
        run_id: str,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        run_crud: RunCRUDDep,
        replayer: EventReplayerDep,
):
    """
    Get reconstructed state for a run by replaying all events.

    This returns the current state including all messages, tool calls,
    and the run status.

    Args:
        run_id: The run ID
        current_user: Current authenticated user
        replayer: Event replayer

    Returns:
        Reconstructed run state
    """
    # Verify run access
    run = await run_crud.get_by_id_and_user(run_id, current_user.id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found or access denied",
        )

    state = await replayer.get_latest_state(run_id)
    return state
