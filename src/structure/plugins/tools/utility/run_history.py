"""Get run event history from Redis stream."""

from pydantic import Field

from structure.core.enums.events import EventType
from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)

# High-frequency noise events excluded by default
_DEFAULT_EXCLUDE: list[str] = [
    EventType.AGENT_TOKEN,
    EventType.AGENT_THINKING,
    EventType.AGENT_HEARTBEAT,
]


class GetRunHistoryTool(InnerTool):
    """Get real-time event history for a run directly from its Redis stream."""

    METADATA = ToolMetadata(
        name="get_run_history",
        display_name="Get Run History",
        description=(
            "Get real-time event history for a run from Redis stream. "
            "Excludes agent_token, agent_thinking and agent_heartbeat by default."
        ),
        category="utility",
        tags=["runs", "history", "events", "redis"],
        timeout=10,
    )

    class InputSchema(ToolInputSchema):
        run_id: str = Field(description="The run UUID to fetch events for")
        limit: int = Field(
            default=100,
            ge=1,
            le=1000,
            description="Maximum number of events to return (most recent N)",
        )
        exclude_types: list[str] = Field(
            default_factory=lambda: list(_DEFAULT_EXCLUDE),
            description=(
                "Event types to exclude. "
                "Defaults to agent_token, agent_thinking, agent_heartbeat."
            ),
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        import redis.asyncio as redis_async

        from structure.config.factory import get_settings
        from structure.models.events.event import Event

        cfg = get_settings().redis
        exclude = frozenset(input_data.exclude_types)

        stream_key = f"{cfg.run_label}:{input_data.run_id}:{cfg.stream_events_suffix}"

        redis_client = redis_async.Redis(
            host=cfg.host,
            port=cfg.port,
            username=cfg.username or None,
            password=cfg.password or None,
            db=cfg.db,
            decode_responses=False,
        )
        try:
            # Run streams are capped at ~1000 entries (maxlen in EventPublisher).
            # Fetch all chronologically, then filter and tail to limit.
            raw_entries: list = await redis_client.xrange(stream_key)
        finally:
            await redis_client.aclose()

        # Parse, filter excluded types, keep last `limit` entries.
        events = []
        for _entry_id, fields in raw_entries:
            try:
                event = Event.from_redis_fields(fields)
            except Exception:
                continue
            if str(event.event_type) not in exclude:
                events.append(event)

        # Tail to limit (most recent N events after filtering)
        if len(events) > input_data.limit:
            events = events[-input_data.limit:]

        return ToolOutputSchema(
            success=True,
            message=f"Retrieved {len(events)} events for run {input_data.run_id}",
            data={
                "events": [
                    {
                        # "id": str(event.id),  # noqa: ERA001
                        "event_type": str(event.event_type),
                        "sequence": event.sequence,
                        "payload": event.payload,
                        # "created_at": (
                        #     event.created_at.isoformat() if event.created_at else None  # noqa: ERA001
                        # ),
                    }
                    for event in events
                ],
                "total": len(events),
                "run_id": input_data.run_id,
                "stream_key": stream_key,
            },
        )
