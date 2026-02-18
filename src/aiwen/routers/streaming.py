"""Combined chat and event streaming endpoints."""

import asyncio
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from aiwen.config.factory import get_settings
from aiwen.core.dependencies.auth import get_current_user
from aiwen.core.dependencies.workspace import (
    EventConsumerDep,
    EventReplayerDep,
    RunCRUDDep,
    RunStateMachineDep,
    WorkspaceCRUDDep,
)
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.events.event_payloads import EventListResponse
from aiwen.services.runs.stuck_run_detector import StuckRunDetector

_redis_cfg = get_settings().redis
EVENT_TYPE_DISCONNECT = _redis_cfg.event_type_disconnect
EVENT_TYPE_ERROR = _redis_cfg.event_type_error
EVENT_TYPE_KEEPALIVE = _redis_cfg.event_type_keepalive

router = APIRouter()


async def _event_stream_generator(
    consumer: Any,
    subscribe_method: str,
    entity_id: str,
    last_id: str = "$",
):
    """Generate SSE events from Redis stream."""
    method = getattr(consumer, subscribe_method)

    try:
        async for event in method(entity_id, last_id=last_id):
            if event.get("type") == EVENT_TYPE_KEEPALIVE:
                yield f": {EVENT_TYPE_KEEPALIVE} {event.get('timestamp', '')}\n\n"
            elif event.get("type") == EVENT_TYPE_ERROR:
                yield f"event: {EVENT_TYPE_ERROR}\ndata: {json.dumps(event)}\n\n"
            else:
                event_type = event.get("event_type", "event")
                yield f"event: {event_type}\ndata: {json.dumps(event)}\n\n"

        # subscribe_run returned naturally (run reached terminal state) — tell the
        # client the stream is done so it does not attempt to reconnect.
        yield f"event: {EVENT_TYPE_DISCONNECT}\ndata: {json.dumps({'reason': 'run_finished'})}\n\n"

    except asyncio.CancelledError:
        yield f"event: {EVENT_TYPE_DISCONNECT}\ndata: {json.dumps({'reason': 'client_disconnected'})}\n\n"


@router.get("/runs/{run_id}/events/stream", tags=["events"])
async def stream_run_events(
    run_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    run_crud: RunCRUDDep,
    consumer: EventConsumerDep,
    last_event_id: str = Query(
        "$", description="Last event ID ($ for new events only)"
    ),
):
    """Stream events for a run via Server-Sent Events (SSE)."""
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


@router.get("/workspaces/{workspace_id}/events/stream", tags=["events"])
async def stream_workspace_events(
    workspace_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    workspace_crud: WorkspaceCRUDDep,
    consumer: EventConsumerDep,
    last_event_id: str = Query("$", description="Last event ID"),
):
    """Stream events for a workspace via Server-Sent Events (SSE)."""
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    return StreamingResponse(
        _event_stream_generator(
            consumer, "subscribe_workspace", workspace_id, last_event_id
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs/{run_id}/events", response_model=EventListResponse, tags=["events"])
async def get_run_events(
    run_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    run_crud: RunCRUDDep,
    replayer: EventReplayerDep,
    from_sequence: int = Query(0, ge=0, description="Start sequence"),
    limit: int = Query(100, ge=1, le=1000, description="Max events to return"),
):
    """Get historical events for a run."""
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


@router.get(
    "/workspaces/{workspace_id}/events",
    response_model=EventListResponse,
    tags=["events"],
)
async def get_workspace_events(
    workspace_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    workspace_crud: WorkspaceCRUDDep,
    replayer: EventReplayerDep,
    from_sequence: int = Query(0, ge=0, description="Start sequence"),
    limit: int = Query(100, ge=1, le=1000, description="Max events"),
    event_types: str | None = Query(None, description="Comma-separated event types"),
):
    """Get historical events for a workspace."""
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


@router.get("/runs/{run_id}/state", tags=["events"])
async def get_run_state(
    run_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    run_crud: RunCRUDDep,
    replayer: EventReplayerDep,
):
    """Get reconstructed state for a run by replaying all events."""
    run = await run_crud.get_by_id_and_user(run_id, current_user.id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found or access denied",
        )

    return await replayer.get_latest_state(run_id)


@router.post("/runs/cleanup-stuck", tags=["events"])
async def cleanup_stuck_runs(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    state_machine: RunStateMachineDep,
    run_crud: RunCRUDDep,
):
    """Detect and recover runs that have been silent for too long."""
    detector = StuckRunDetector(state_machine)
    recovered_ids = await detector.detect_and_recover(run_crud.db)
    return {
        "recovered_count": len(recovered_ids),
        "recovered_run_ids": recovered_ids,
    }
