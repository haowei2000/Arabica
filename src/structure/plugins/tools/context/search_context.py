"""Search context tool."""

from typing import Literal

from pydantic import Field

from structure.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)
from structure.extensions.database import get_session
from structure.plugins.tools.context._sql_context import (
    normalize_level,
    publish_context_using,
    search_contexts,
)


class SearchContextTool(InnerTool):
    """Search scoped SQL-backed context content using regular expressions."""

    METADATA = ToolMetadata(
        name="search_context",
        display_name="Search Context",
        description="Search scoped context paths, glances, and content using regex",
        category="context",
        tags=["search", "regex", "context", "grep"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="Workspace ID")
        run_id: str | None = Field(default=None, description="Run ID")
        user_id: str | None = Field(default=None, description="User ID")
        pattern: str = Field(description="Regular expression pattern to search for")
        level: Literal["glance", "overview", "detail"] = Field(
            default="overview",
            description="Disclosure level to return for matched contexts",
        )
        context_type: str | None = Field(
            default=None,
            description="Optional context type filter (conversation, tool, knowledge)",
        )
        min_rating: float | None = Field(
            default=None,
            ge=-1.0,
            le=1.0,
            description="Optional minimum average context rating",
        )
        max_results: int = Field(default=10, ge=1, le=100, description="Max results")
        ignore_case: bool = Field(
            default=True, description="Whether to ignore case in pattern matching"
        )
        multiline: bool = Field(
            default=False, description="Whether to enable multiline mode"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from re import error as RegexError
        from uuid import UUID

        try:
            workspace_id = UUID(input_data.workspace_id)
            level = normalize_level(input_data.level)

            async with get_session("structure") as session:
                contexts = await search_contexts(
                    session,
                    workspace_id,
                    pattern=input_data.pattern,
                    level=level,
                    user_id=input_data.user_id,
                    limit=input_data.max_results,
                    context_type=input_data.context_type,
                    ignore_case=input_data.ignore_case,
                    multiline=input_data.multiline,
                    min_rating=input_data.min_rating,
                )
                paths = [ctx.get("path", "") for ctx in contexts if ctx.get("path")]
                await publish_context_using(
                    session,
                    workspace_id,
                    operation="search",
                    paths=paths,
                    level=level,
                    run_id=input_data.run_id,
                    user_id=input_data.user_id,
                    meta={"pattern": input_data.pattern},
                )
                await session.commit()

            return ToolOutputSchema(
                success=True,
                message=f"Found {len(contexts)} matching contexts",
                data={
                    "matches": contexts,
                    "total_contexts_matched": len(contexts),
                    "pattern": input_data.pattern,
                    "options": {
                        "ignore_case": input_data.ignore_case,
                        "multiline": input_data.multiline,
                        "level": level,
                    },
                },
            )

        except RegexError as e:
            return ToolOutputSchema(
                success=False,
                message=f"Invalid regex pattern: {e}",
                error=str(e),
                data={"pattern": input_data.pattern, "matches": []},
            )
        except Exception as e:
            return ToolOutputSchema(
                success=False,
                message=f"Search failed: {e!s}",
                error=str(e),
                data={"pattern": input_data.pattern},
            )
