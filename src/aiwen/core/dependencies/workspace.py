# aiwen/dependencies/workspace.py
"""Dependency injection functions for workspace-related services."""

from typing import Annotated

from fastapi import Depends
import redis.asyncio as redis_async
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.extensions.database import get_aiwen_db
from aiwen.middleware.cache_middleware import get_redis_client
from aiwen.services.events.event_consumer import EventConsumer, EventReplayer
from aiwen.services.events.event_crud import EventCRUD
from aiwen.services.events.event_publisher import EventPublisher
from aiwen.services.runs.run_crud import RunCRUD
from aiwen.services.runs.run_state_machine import RunStateMachine
from aiwen.services.workspaces.member_crud import WorkspaceMemberCRUD
from aiwen.services.workspaces.workspace_crud import WorkspaceCRUD


async def _get_redis_client() -> redis_async.Redis:
    """Get Redis async client."""
    return get_redis_client(is_async=True)


async def get_workspace_crud(
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
) -> WorkspaceCRUD:
    """Dependency to get WorkspaceCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        WorkspaceCRUD instance
    """
    return WorkspaceCRUD(db)


async def get_workspace_member_crud(
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
) -> WorkspaceMemberCRUD:
    """Dependency to get WorkspaceMemberCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        WorkspaceMemberCRUD instance
    """
    return WorkspaceMemberCRUD(db)


async def get_run_crud(
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
) -> RunCRUD:
    """Dependency to get RunCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        RunCRUD instance
    """
    return RunCRUD(db)


async def get_event_publisher(
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
    redis_client: Annotated[redis_async.Redis, Depends(_get_redis_client)],
) -> EventPublisher:
    """Dependency to get EventPublisher instance.

    Args:
        db: Database session from dependency injection
        redis_client: Redis async client

    Returns:
        EventPublisher instance
    """
    return EventPublisher(db, redis_client)


async def get_event_consumer(
    redis_client: Annotated[redis_async.Redis, Depends(_get_redis_client)],
) -> EventConsumer:
    """Dependency to get EventConsumer instance.

    Args:
        redis_client: Redis async client

    Returns:
        EventConsumer instance
    """
    return EventConsumer(redis_client)


async def get_event_replayer(
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
) -> EventReplayer:
    """Dependency to get EventReplayer instance.

    Args:
        db: Database session from dependency injection

    Returns:
        EventReplayer instance
    """
    return EventReplayer(db)


async def get_event_crud(
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
) -> EventCRUD:
    """Dependency to get EventCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        EventCRUD instance
    """
    return EventCRUD(db)


async def get_run_state_machine(
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
    redis_client: Annotated[redis_async.Redis, Depends(_get_redis_client)],
) -> RunStateMachine:
    """Dependency to get RunStateMachine instance.

    Args:
        db: Database session from dependency injection
        redis_client: Redis async client

    Returns:
        RunStateMachine instance
    """
    return RunStateMachine(db, redis_client)


# Type aliases for Annotated dependencies
WorkspaceCRUDDep = Annotated[WorkspaceCRUD, Depends(get_workspace_crud)]
WorkspaceMemberCRUDDep = Annotated[
    WorkspaceMemberCRUD, Depends(get_workspace_member_crud)
]
RunCRUDDep = Annotated[RunCRUD, Depends(get_run_crud)]
EventCRUDDep = Annotated[EventCRUD, Depends(get_event_crud)]
EventPublisherDep = Annotated[EventPublisher, Depends(get_event_publisher)]
EventConsumerDep = Annotated[EventConsumer, Depends(get_event_consumer)]
EventReplayerDep = Annotated[EventReplayer, Depends(get_event_replayer)]
RunStateMachineDep = Annotated[RunStateMachine, Depends(get_run_state_machine)]
