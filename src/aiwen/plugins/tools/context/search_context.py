"""Search context tool."""

import re

from pydantic import Field

from aiwen.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class SearchContextTool(InnerTool):
    """Search context content using regular expressions"""

    METADATA = ToolMetadata(
        name="search_context",
        display_name="Search Context",
        description="Search through context content using regex patterns (grep-like)",
        category="context",
        tags=["search", "regex", "context", "grep"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        pattern: str = Field(description="Regular expression pattern to search for")
        context_id: str | None = Field(
            default=None, description="Optional specific context ID to search in"
        )
        user_id: str | None = Field(
            default=None, description="Optional user ID to scope the search"
        )
        context_type: str | None = Field(
            default=None,
            description="Optional context type filter (conversation, tool, knowledge)",
        )
        max_results: int = Field(
            default=10,
            ge=1,
            le=100,
            description="Maximum number of matching contexts to return",
        )
        context_chars: int = Field(
            default=100,
            ge=0,
            le=500,
            description="Number of characters to show before/after match",
        )
        ignore_case: bool = Field(
            default=True, description="Whether to ignore case in pattern matching"
        )
        multiline: bool = Field(
            default=False, description="Whether to enable multiline mode"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from sqlalchemy import select

        from aiwen.extensions.database import get_session
        from aiwen.models.context.context import Context

        flags = 0
        if input_data.ignore_case:
            flags |= re.IGNORECASE
        if input_data.multiline:
            flags |= re.MULTILINE | re.DOTALL

        try:
            regex = re.compile(input_data.pattern, flags)
        except re.error as e:
            return ToolOutputSchema(
                success=False,
                message=f"Invalid regex pattern: {e}",
                error=str(e),
                data={
                    "pattern": input_data.pattern,
                    "matches": [],
                    "total_matches": 0,
                },
            )

        async with get_session("aiwen") as db:
            query = select(Context)

            if input_data.context_id:
                query = query.where(Context.id == input_data.context_id)
            if input_data.user_id:
                query = query.where(Context.user_id == input_data.user_id)
            if input_data.context_type:
                query = query.where(Context.context_type == input_data.context_type)

            query = query.order_by(Context.created_at.desc())

            result = await db.execute(query)
            contexts = result.scalars().all()

            matches = []
            total_match_count = 0

            for ctx in contexts:
                if not ctx.content:
                    continue

                context_matches = []
                for match in regex.finditer(ctx.content):
                    start, end = match.span()
                    matched_text = match.group(0)

                    context_start = max(0, start - input_data.context_chars)
                    context_end = min(len(ctx.content), end + input_data.context_chars)

                    before = ctx.content[context_start:start]
                    after = ctx.content[end:context_end]

                    if context_start > 0:
                        before = "..." + before
                    if context_end < len(ctx.content):
                        after = after + "..."

                    context_matches.append(
                        {
                            "matched_text": matched_text,
                            "before_context": before,
                            "after_context": after,
                            "char_position": start,
                            "match_length": len(matched_text),
                            "line_number": ctx.content[:start].count("\n") + 1,
                        }
                    )

                if context_matches:
                    total_match_count += len(context_matches)

                    matches.append(
                        {
                            "context_id": str(ctx.id),
                            "context_type": ctx.context_type,
                            "user_id": str(ctx.user_id),
                            "source_id": str(ctx.source_id) if ctx.source_id else None,
                            "created_at": ctx.created_at.isoformat()
                            if ctx.created_at
                            else None,
                            "summary": ctx.summary,
                            "keywords": ctx.keywords,
                            "importance": ctx.importance,
                            "match_count": len(context_matches),
                            "matches": context_matches,
                        }
                    )

                    if len(matches) >= input_data.max_results:
                        break

            return ToolOutputSchema(
                success=True,
                message=f"Found {total_match_count} matches in {len(matches)} contexts",
                data={
                    "matches": matches,
                    "total_contexts_matched": len(matches),
                    "total_pattern_matches": total_match_count,
                    "contexts_searched": len(contexts),
                    "pattern": input_data.pattern,
                    "options": {
                        "ignore_case": input_data.ignore_case,
                        "multiline": input_data.multiline,
                        "context_chars": input_data.context_chars,
                    },
                },
            )
