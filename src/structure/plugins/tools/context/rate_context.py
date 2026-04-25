"""Rate a context source after using it.

Agents call ``rate_context`` after they have consulted an entry (tool
schema, knowledge chunk, prior observation) to report how useful it was.
The rating is persisted as a ``CONTEXT_RATED`` event and also folded into
the context row's denormalised ``rating_sum`` / ``rating_count`` aggregate
so future retrieval can order by source quality without replaying the
event log.
"""

from pydantic import Field

from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class RateContextTool(InnerTool):
    METADATA = ToolMetadata(
        name="rate_context",
        display_name="Rate Context",
        description=(
            "Rate a context source after using it. Emits a CONTEXT_RATED "
            "event and updates the context's rating aggregate."
        ),
        category="context",
        tags=["context", "rate", "feedback", "evaluation"],
        timeout=10,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        path: str = Field(description="Context path that was consulted")
        rating: float = Field(
            description="Usefulness rating in [-1.0, 1.0]. Positive = helpful, "
            "negative = misleading, 0 = neutral.",
            ge=-1.0,
            le=1.0,
        )
        comment: str | None = Field(
            default=None,
            description="Optional short note explaining the rating.",
            max_length=500,
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from uuid import UUID

        from sqlalchemy import select

        from structure.core.enums import EventType
        from structure.extensions.database import get_session
        from structure.models.context.context import Context
        from structure.services.events.event_publisher import EventPublisher

        try:
            workspace_id = UUID(input_data.workspace_id)
        except ValueError as exc:
            return ToolOutputSchema(
                success=False,
                message=f"Invalid workspace_id: {exc}",
                error=str(exc),
                data={"path": input_data.path},
            )

        try:
            async with get_session("structure") as session:
                stmt = select(Context).where(
                    Context.source_id == workspace_id,
                    Context.path == input_data.path,
                )
                result = await session.execute(stmt)
                ctx = result.scalars().first()

                if ctx is not None:
                    ctx.record_rating(input_data.rating)

                publisher = EventPublisher(db=session)
                await publisher.publish(
                    event_type=EventType.CONTEXT_RATED,
                    workspace_id=workspace_id,
                    payload={
                        "path": input_data.path,
                        "rating": input_data.rating,
                        "comment": input_data.comment,
                    },
                    auto_commit=True,
                )

            rating_avg = ctx.rating_avg if ctx is not None else None
            rating_count = ctx.rating_count if ctx is not None else 0
            return ToolOutputSchema(
                success=True,
                message=f"Rated {input_data.path} = {input_data.rating}",
                data={
                    "path": input_data.path,
                    "rating_avg": rating_avg,
                    "rating_count": rating_count,
                    "context_found": ctx is not None,
                },
            )
        except Exception as exc:
            return ToolOutputSchema(
                success=False,
                message=f"Failed to rate context: {exc}",
                error=str(exc),
                data={"path": input_data.path},
            )
