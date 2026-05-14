"""Get workspace-level conversation history from the database."""

from pydantic import Field

from structure.core.enums.events import EventType
from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)

# Only load high-level conversation turns for cross-run workspace history.
# Tool events (TOOL_RESULT, TOOL_ERROR) are excluded because they belong to
# the specific run that produced them and add noise without meaningful context
# when loading across runs.
_IMPORTANT_TYPES: list[str] = [
    EventType.USER_MESSAGE,
    EventType.AGENT_MESSAGE,
    EventType.TOOL_RESULT,
    EventType.TOOL_ERROR,
    EventType.USER_FEEDBACK,
]


class GetWorkspaceHistoryTool(InnerTool):
    """Load cross-run conversation history for a workspace from the database.

    Used by the executor when ``global_event=True`` in AppConfig.  Queries
    the persistent event table so history survives Redis stream eviction and
    includes events from all previous runs in the workspace.
    """

    METADATA = ToolMetadata(
        name="get_workspace_history",
        display_name="Get Workspace History",
        description=(
            "Get conversation history (user messages, agent replies, tool results) "
            "across all runs in a workspace. Used when global_event is enabled."
        ),
        category="utility",
        tags=["workspace", "history", "events", "global"],
        timeout=15,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="The workspace UUID to fetch history for")
        limit: int = Field(
            default=200,
            ge=1,
            le=500,
            description="Maximum number of events to return (oldest-to-newest)",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from sqlalchemy import and_, select

        from structure.extensions.database import get_session
        from structure.models.events.event import Event

        try:
            async with get_session("structure") as db:
                stmt = (
                    select(Event)
                    .where(
                        and_(
                            Event.workspace_id == input_data.workspace_id,
                            Event.event_type.in_(_IMPORTANT_TYPES),
                            Event.is_archived.is_(False),
                        )
                    )
                    .order_by(Event.created_at.asc())
                    .limit(input_data.limit)
                )
                result = await db.execute(stmt)
                events = list(result.scalars().all())

            return ToolOutputSchema(
                success=True,
                message=f"Retrieved {len(events)} events for workspace {input_data.workspace_id}",
                data={
                    "events": [
                        {
                            "event_type": str(event.event_type),
                            "sequence": event.sequence,
                            "payload": event.payload,
                        }
                        for event in events
                    ],
                    "total": len(events),
                    "workspace_id": input_data.workspace_id,
                },
            )

        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to load workspace history: {e!s}",
                error=str(e),
                data={"workspace_id": input_data.workspace_id},
            )
