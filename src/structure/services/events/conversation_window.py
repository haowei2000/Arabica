"""Cached workspace conversation-window read model."""

from __future__ import annotations

import logging
import os

from cachetools import TTLCache
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.models.events.event import Event
from structure.schemas.events.event_payloads import EventType

logger = logging.getLogger(__name__)

_CACHE_MAXSIZE = int(os.getenv("CONVERSATION_WINDOW_CACHE_SIZE", "500"))
_CACHE_TTL = int(os.getenv("CONVERSATION_WINDOW_CACHE_TTL", "120"))

type CacheKey = tuple[str, int]
_CONVERSATION_WINDOW_CACHE: TTLCache[CacheKey, list[Event]] = TTLCache(
    maxsize=_CACHE_MAXSIZE,
    ttl=_CACHE_TTL,
)

CONVERSATION_WINDOW_EVENT_TYPES = frozenset(
    {
        str(EventType.USER_MESSAGE),
        str(EventType.AGENT_MESSAGE),
        str(EventType.TOOL_RESULT),
        str(EventType.TOOL_ERROR),
        str(EventType.USER_FEEDBACK),
        str(EventType.TOOL_CALL),
    }
)


async def get_conversation_window(
    db: AsyncSession,
    workspace_id: str,
    *,
    limit: int,
) -> list[Event]:
    """Return recent conversation events for a workspace."""
    key = (workspace_id, limit)
    if key in _CONVERSATION_WINDOW_CACHE:
        logger.debug(
            "Conversation window cache hit workspace=%s limit=%s "
            "conversation_window_cache_hit=true",
            workspace_id,
            limit,
        )
        return list(_CONVERSATION_WINDOW_CACHE[key])

    logger.debug(
        "Conversation window cache miss workspace=%s limit=%s "
        "conversation_window_cache_hit=false",
        workspace_id,
        limit,
    )
    stmt = (
        select(Event)
        .where(
            Event.workspace_id == workspace_id,
            Event.event_type.in_(CONVERSATION_WINDOW_EVENT_TYPES),
            Event.is_archived.is_(False),
        )
        .order_by(Event.created_at.desc(), Event.sequence.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    rows.reverse()
    _CONVERSATION_WINDOW_CACHE[key] = rows
    return list(rows)


def record_conversation_event(event: Event) -> None:
    """Append a newly committed conversation event into cached windows."""
    if str(event.event_type) not in CONVERSATION_WINDOW_EVENT_TYPES:
        return
    workspace_id = str(event.workspace_id)
    for key in list(_CONVERSATION_WINDOW_CACHE.keys()):
        cached_workspace_id, limit = key
        if cached_workspace_id != workspace_id:
            continue
        rows = [row for row in _CONVERSATION_WINDOW_CACHE[key] if row.id != event.id]
        rows.append(event)
        _CONVERSATION_WINDOW_CACHE[key] = rows[-limit:]


def invalidate_conversation_window(workspace_id: str) -> None:
    """Drop cached windows for one workspace."""
    for key in list(_CONVERSATION_WINDOW_CACHE.keys()):
        if key[0] == workspace_id:
            del _CONVERSATION_WINDOW_CACHE[key]


def clear_conversation_window_cache() -> None:
    _CONVERSATION_WINDOW_CACHE.clear()
