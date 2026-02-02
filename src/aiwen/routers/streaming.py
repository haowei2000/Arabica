"""Combined chat and event streaming endpoints."""

import asyncio
import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from aiwen.dependencies.agents import (
    get_conversation_crud,
    get_task_consumer,
    get_task_crud,
    get_task_producer,
)
from aiwen.dependencies.auth import get_current_user, get_token_data
from aiwen.dependencies.workspace import (
    EventConsumerDep,
    EventReplayerDep,
    RunCRUDDep,
    WorkspaceCRUDDep,
)
from aiwen.schemas.agents.input import TextInput
from aiwen.schemas.auth.auth import TokenData
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.events.event_payloads import EventListResponse
from aiwen.services.agent.chat.chat_service import (
    StartTaskResult,
    get_messages_by_task,
    start_task,
)
from aiwen.services.crud.conversation_crud import ConversationCRUD
from aiwen.services.crud.task_crud import AgentTaskCRUD
from aiwen.workers.task_consumer import AgentTaskConsumer
from aiwen.workers.task_producer import AgentTaskProducer

router = APIRouter()


@router.post("/chat/app/{app_id}/stream", response_model=StartTaskResult, tags=["chat"])
@router.post("/agent/chat/app/{app_id}/stream", response_model=StartTaskResult, tags=["chat"])
async def start_chat_task(
    app_id: UUID,
    text_input: TextInput,
    token_data: Annotated[TokenData, Depends(get_token_data)],
    task_crud: AgentTaskCRUD = Depends(get_task_crud),
    conversation_crud: ConversationCRUD = Depends(get_conversation_crud),
    task_producer: AgentTaskProducer = Depends(get_task_producer),
) -> StartTaskResult:
    """Start a chat task and return task/conversation/message identifiers."""
    user_id = UUID(token_data.user_id)
    text_input.from_account_id = user_id
    return await start_task(
        app_id=app_id,
        user_id=user_id,
        text_input=text_input,
        task_crud=task_crud,
        conversation_crud=conversation_crud,
        task_producer=task_producer,
    )


@router.get("/chat/task/{task_id}/stream", tags=["chat"])
@router.get("/agent/chat/task/{task_id}/stream", tags=["chat"])
async def stream_task_messages(
    task_id: UUID,
    token_data: Annotated[TokenData, Depends(get_token_data)],
    task_crud: AgentTaskCRUD = Depends(get_task_crud),
    task_consumer: AgentTaskConsumer = Depends(get_task_consumer),
):
    """Stream task messages from Redis as Server-Sent Events."""
    task = await task_crud.get_agent_task_by_task_id(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    if task.user_id and str(task.user_id) != token_data.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to access this task",
        )

    return StreamingResponse(
        get_messages_by_task(
            task_id=task_id,
            task_consumer=task_consumer,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )


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
            if event.get("type") == "keepalive":
                yield f": keepalive {event.get('timestamp', '')}\n\n"
            elif event.get("type") == "error":
                yield f"event: error\ndata: {json.dumps(event)}\n\n"
            else:
                event_type = event.get("event_type", "event")
                yield f"event: {event_type}\ndata: {json.dumps(event)}\n\n"

    except asyncio.CancelledError:
        yield f"event: disconnect\ndata: {json.dumps({'reason': 'client_disconnected'})}\n\n"


@router.get("/runs/{run_id}/events/stream", tags=["events"])
async def stream_run_events(
    run_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    run_crud: RunCRUDDep,
    consumer: EventConsumerDep,
    last_event_id: str = Query("$", description="Last event ID ($ for new events only)"),
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
        _event_stream_generator(consumer, "subscribe_workspace", workspace_id, last_event_id),
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


@router.get("/workspaces/{workspace_id}/events", response_model=EventListResponse, tags=["events"])
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
