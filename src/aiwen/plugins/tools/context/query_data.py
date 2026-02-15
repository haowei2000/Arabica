"""Query structured data tool."""

from typing import Any

from pydantic import Field

from aiwen.interfaces.tool import InnerTool, ToolInputSchema, ToolMetadata, ToolOutputSchema


class QueryStructuredDataTool(InnerTool):
    """Execute a read-only SQL query on structured data"""

    METADATA = ToolMetadata(
        name="query_structured_data",
        display_name="Query Structured Data",
        description="Execute a read-only SQL query (SELECT only) on database",
        category="context",
        tags=["sql", "database", "query", "data"],
        timeout=60,
    )

    class InputSchema(ToolInputSchema):
        sql: str = Field(description="SQL SELECT query (only SELECT allowed)")
        database: str = Field(
            default="aiwen", description='Database name ("aiwen", "mes", etc.)'
        )
        params: dict[str, Any] | None = Field(
            default=None, description="Query parameters for safe binding"
        )
        max_rows: int = Field(
            default=100, ge=1, le=1000, description="Maximum rows to return"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        sql_upper = input_data.sql.strip().upper()
        if not sql_upper.startswith("SELECT"):
            return ToolOutputSchema(
                success=False,
                message="Only SELECT queries are allowed",
                error="Query must start with SELECT",
                data={"columns": [], "rows": [], "row_count": 0},
            )

        dangerous = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE", "EXEC"]
        for keyword in dangerous:
            if keyword in sql_upper:
                return ToolOutputSchema(
                    success=False,
                    message=f"Query contains forbidden keyword: {keyword}",
                    error=f"Keyword '{keyword}' is not allowed",
                    data={"columns": [], "rows": [], "row_count": 0},
                )

        from sqlalchemy import text

        from aiwen.extensions.database import get_session

        async with get_session(input_data.database) as db:
            if "LIMIT" not in sql_upper:
                input_data.sql = f"{input_data.sql} LIMIT {input_data.max_rows}"

            result = await db.execute(text(input_data.sql), input_data.params or {})
            rows = result.fetchall()
            columns = list(result.keys()) if rows else []

            return ToolOutputSchema(
                success=True,
                message=f"Query executed successfully, returned {len(rows)} rows",
                data={
                    "columns": columns,
                    "rows": [list(row) for row in rows],
                    "row_count": len(rows),
                },
            )
