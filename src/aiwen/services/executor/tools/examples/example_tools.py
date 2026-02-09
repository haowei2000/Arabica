"""
Example Tool Collection

Demonstrates how to create tools with different execution modes using the new base class system.
"""

import asyncio
import logging
from typing import Any

from pydantic import Field

from aiwen.services.executor.tools.base_tool import (
    InnerTool,
    CeleryConfig,
    ClientConfig,
    ContainerConfig,
    HTTPConfig,
    ResourceLimits,
    ToolExecutionMode,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)
from aiwen.services.executor.tools.tool_registry import register_tool

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SERVER_RUN Tool Example
# ═══════════════════════════════════════════════════════════════════════════════


@register_tool
class DatabaseQueryTool(InnerTool):
    """Database Query Tool - Server-side direct execution"""

    METADATA = ToolMetadata(
        name="database_query",
        display_name="Database Query",
        description="Execute read-only queries in database (SELECT statements only)",
        version="1.0.0",
        category="database",
        tags=["database", "query", "sql"],
        execution_mode=ToolExecutionMode.SERVER_RUN,
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        sql: str = Field(description="SQL query statement (only supports SELECT)")
        database: str = Field(default="aiwen", description="Database name")
        max_rows: int = Field(default=100, description="Maximum number of rows to return")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        """Execute database query"""
        from sqlalchemy import text

        from aiwen.extensions.database import get_session

        # Security check: only allow SELECT
        sql_upper = input_data.sql.strip().upper()
        if not sql_upper.startswith("SELECT"):
            return ToolOutputSchema(
                success=False, message="Only SELECT queries are allowed", error="Invalid SQL"
            )

        # Execute query
        async with get_session(input_data.database) as db:
            result = await db.execute(
                text(f"{input_data.sql} LIMIT {input_data.max_rows}")
            )
            rows = result.fetchall()
            columns = list(result.keys()) if rows else []

            return ToolOutputSchema(
                success=True,
                message=f"Query returned {len(rows)} rows of data",
                data={
                    "columns": columns,
                    "rows": [list(row) for row in rows],
                    "row_count": len(rows),
                },
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. HTTP Tool Example
# ═══════════════════════════════════════════════════════════════════════════════


@register_tool
class WeatherAPITool(InnerTool):
    """Weather Query Tool - HTTP API call"""

    METADATA = ToolMetadata(
        name="weather_api",
        display_name="Weather Query",
        description="Query weather information for specified city via HTTP API",
        version="1.0.0",
        category="api",
        tags=["weather", "http", "api"],
        execution_mode=ToolExecutionMode.HTTP,
        timeout=15,
        http_config=HTTPConfig(
            method="GET",
            url="https://api.openweathermap.org/data/2.5/weather",
            headers={"Accept": "application/json"},
            timeout=15,
            retry_times=3,
        ),
    )

    class InputSchema(ToolInputSchema):
        city: str = Field(description="City name (English)")
        api_key: str = Field(description="OpenWeather API key")
        units: str = Field(default="metric", description="Unit system (metric/imperial)")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        """Call weather API"""
        import httpx

        http_config = self.METADATA.http_config
        if not http_config:
            return ToolOutputSchema(
                success=False, error="HTTP config not found"
            )

        params = {
            "q": input_data.city,
            "appid": input_data.api_key,
            "units": input_data.units,
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(
                http_config.url,
                params=params,
                headers=http_config.headers,
                timeout=http_config.timeout,
            )

            if response.status_code == 200:
                data = response.json()
                return ToolOutputSchema(
                    success=True,
                    message=f"Successfully obtained {input_data.city} 's weather information",
                    data={
                        "city": data.get("name"),
                        "temperature": data["main"]["temp"],
                        "feels_like": data["main"]["feels_like"],
                        "humidity": data["main"]["humidity"],
                        "description": data["weather"][0]["description"],
                    },
                )
            else:
                return ToolOutputSchema(
                    success=False,
                    message="API call failed",
                    error=f"HTTP {response.status_code}: {response.text}",
                )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CONTAINER_RUN Tool Example
# ═══════════════════════════════════════════════════════════════════════════════


@register_tool
class PythonCodeExecutorTool(InnerTool):
    """Python Code Executor - Container isolated execution"""

    METADATA = ToolMetadata(
        name="python_executor",
        display_name="Python Code Executor",
        description="Execute Python code safely in isolated Docker container",
        version="1.0.0",
        category="code_execution",
        tags=["python", "code", "sandbox", "container"],
        execution_mode=ToolExecutionMode.CONTAINER_RUN,
        timeout=60,
        container_config=ContainerConfig(
            image="python:3.12-slim",
            workdir="/workspace",
            resource_limits=ResourceLimits(
                memory="512m",
                cpu_quota=50000,
                network_enabled=False,
                read_only_rootfs=True,
            ),
            environment={"PYTHONUNBUFFERED": "1"},
        ),
    )

    class InputSchema(ToolInputSchema):
        code: str = Field(description="Python code to execute")
        timeout: int = Field(default=30, description="Execution timeout (seconds)")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        """Execute Python code in container"""
        # Note: Actual container execution logic should be handled by ExecutionRouter
        # This just returns a placeholder result
        logger.info(f"Executing Python code in container: {input_data.code[:100]}...")

        # Simulate execution (actual implementation should call Docker API)
        await asyncio.sleep(0.5)

        return ToolOutputSchema(
            success=True,
            message="Code executed successfully",
            data={
                "stdout": "Hello from container!",
                "stderr": "",
                "exit_code": 0,
                "execution_time": 0.5,
            },
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. CLIENT_RUN Tool Example
# ═══════════════════════════════════════════════════════════════════════════════


@register_tool
class FilePickerTool(InnerTool):
    """File Picker - Client-side execution"""

    METADATA = ToolMetadata(
        name="file_picker",
        display_name="File Picker",
        description="Open file selection dialog in user browser",
        version="1.0.0",
        category="file",
        tags=["file", "client", "browser"],
        execution_mode=ToolExecutionMode.CLIENT_RUN,
        timeout=120,
        client_config=ClientConfig(
            handler_name="filePicker",
            config={
                "multiple": False,
                "accept": [".pdf", ".txt", ".doc", ".docx"],
            },
            require_user_approval=True,
        ),
    )

    class InputSchema(ToolInputSchema):
        allowed_extensions: list[str] = Field(
            default=[".pdf", ".txt", ".doc", ".docx"],
            description="List of allowed file extensions",
        )
        multiple: bool = Field(default=False, description="Whether multiple file selection is allowed")
        max_size_mb: int = Field(default=10, description="Maximum file size (MB)")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        """Trigger client file picker"""
        # Note: Actual client execution logic should be handled by ClientExecutor
        # This just returns a placeholder result
        logger.info(f"Opening file picker with extensions: {input_data.allowed_extensions}")

        # Simulate waiting for user selection
        await asyncio.sleep(1)

        return ToolOutputSchema(
            success=True,
            message="User has selected file(s)",
            data={
                "files": [
                    {
                        "name": "example.pdf",
                        "size": 1024000,
                        "type": "application/pdf",
                        "path": "/uploads/example.pdf",
                    }
                ]
            },
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CELERY_RUN Tool Example
# ═══════════════════════════════════════════════════════════════════════════════


@register_tool
class ReportGeneratorTool(InnerTool):
    """Report Generator - Celery async task"""

    METADATA = ToolMetadata(
        name="report_generator",
        display_name="Report Generator",
        description="Asynchronously generate detailed report (may take several minutes)",
        version="1.0.0",
        category="report",
        tags=["report", "async", "celery", "long-running"],
        execution_mode=ToolExecutionMode.CELERY_RUN,
        timeout=1800,  # 30 minutes
        celery_config=CeleryConfig(
            queue="slow",
            priority=3,
            progress_enabled=True,
            retry_on_failure=True,
            max_retries=2,
        ),
    )

    class InputSchema(ToolInputSchema):
        report_type: str = Field(description="Report type (daily/weekly/monthly)")
        start_date: str = Field(description="Start date (YYYY-MM-DD)")
        end_date: str = Field(description="End date (YYYY-MM-DD)")
        format: str = Field(default="pdf", description="Output format (pdf/excel/html)")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        """Generate report (async task)"""
        # Note: Actual Celery task execution should be handled by AsyncExecutor
        # This just simulates a long-running task
        logger.info(
            f"Generating {input_data.report_type} report "
            f"from {input_data.start_date} to {input_data.end_date}"
        )

        # Simulate progress updates
        for progress in [20, 40, 60, 80, 100]:
            await asyncio.sleep(0.5)
            logger.info(f"Report generation progress: {progress}%")

        return ToolOutputSchema(
            success=True,
            message="Report generation completed",
            data={
                "report_url": f"/reports/report_{input_data.report_type}.{input_data.format}",
                "file_size": 1024000,
                "page_count": 15,
                "generated_at": "2026-02-08T10:30:00Z",
            },
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Advanced Example: Tool with custom hooks
# ═══════════════════════════════════════════════════════════════════════════════


@register_tool
class AdvancedSearchTool(InnerTool):
    """Advanced Search Tool - Demonstrates hook method usage"""

    METADATA = ToolMetadata(
        name="advanced_search",
        display_name="Advanced Search",
        description="Execute advanced search in knowledge base with permission control and logging",
        version="1.0.0",
        category="search",
        tags=["search", "advanced", "knowledge"],
        execution_mode=ToolExecutionMode.SERVER_RUN,
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        query: str = Field(description="Search query")
        user_id: str = Field(description="User ID")
        filters: dict[str, Any] = Field(default_factory=dict, description="Search filters")
        limit: int = Field(default=10, description="Maximum number of results")

    async def before_execute(self, input_data: InputSchema) -> None:
        """Before execution: Permission check and logging"""
        logger.info(
            f"User {input_data.user_id} searching for: {input_data.query}"
        )

        # Simulate permission check
        # if not await check_user_permission(input_data.user_id, "search"):
        #     raise PermissionError("User does not have search permission")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        """Execute search"""
        # Simulate search logic
        await asyncio.sleep(0.3)

        results = [
            {"id": "1", "title": "Result 1", "score": 0.95},
            {"id": "2", "title": "Result 2", "score": 0.87},
        ]

        return ToolOutputSchema(
            success=True,
            message=f"Found {len(results)} results",
            data={"results": results, "total": len(results), "query": input_data.query},
        )

    async def after_execute(
        self, input_data: InputSchema, output: ToolOutputSchema
    ) -> None:
        """After execution: Record statistics"""
        if output.success and output.data:
            result_count = output.data.get("total", 0)
            logger.info(
                f"Search completed: user={input_data.user_id}, "
                f"query={input_data.query}, results={result_count}"
            )

            # Update search statistics
            # await update_search_stats(input_data.user_id, input_data.query, result_count)

    async def on_error(
        self, input_data: InputSchema | None, error: Exception
    ) -> ToolOutputSchema:
        """Error handling: Log error and return friendly message"""
        logger.error(f"Search failed: {error}", exc_info=True)

        return ToolOutputSchema(
            success=False,
            message="Search failed, please try again later",
            error=str(error),
            data={"error_type": type(error).__name__},
        )
