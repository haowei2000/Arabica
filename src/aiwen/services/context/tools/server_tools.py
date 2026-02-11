"""
Server-side tools using BaseTool class system

Migrated from LangChain @tool decorator to unified BaseTool interface.
All tools execute directly in the API server process.
"""

from datetime import UTC, datetime
import logging
from typing import Any

from pydantic import Field
from aiwen.registries.core import register_tool


from aiwen.services.context.tools.base_tool import (
    ClientConfig,
    ContainerConfig,
    InnerTool,
    ResourceLimits,
    ToolExecutionMode,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)
import contextlib

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@register_tool
class GetCurrentTimeTool(InnerTool):
    """Get the current server time"""

    METADATA = ToolMetadata(
        name="get_current_time",
        display_name="Get Current Time",
        description="Get the current server time with timezone support",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="utility",
        tags=["time", "datetime", "utility"],
        timeout=5,
    )

    class InputSchema(ToolInputSchema):
        timezone: str = Field(
            default="UTC", description='Timezone name (e.g., "UTC", "Asia/Shanghai")'
        )
        format: str = Field(
            default="%Y-%m-%d %H:%M:%S", description="Output format string"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from zoneinfo import ZoneInfo

        try:
            zone = ZoneInfo(input_data.timezone)
        except Exception:
            zone = UTC
            input_data.timezone = "UTC"

        now = datetime.now(zone)

        return ToolOutputSchema(
            success=True,
            message=f"Current time in {input_data.timezone}",
            data={
                "time": now.strftime(input_data.format),
                "timestamp": int(now.timestamp()),
                "timezone": input_data.timezone,
                "iso": now.isoformat(),
            },
        )

@register_tool
class CacheGetTool(InnerTool):
    """Get a value from the cache"""

    METADATA = ToolMetadata(
        name="cache_get",
        display_name="Cache Get",
        description="Get a value from the Redis cache",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="utility",
        tags=["cache", "redis", "storage"],
        timeout=5,
    )

    class InputSchema(ToolInputSchema):
        key: str = Field(description="Cache key")
        namespace: str = Field(
            default="default", description="Cache namespace for isolation"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from aiwen.middleware.cache_middleware import get_redis_client

        redis = get_redis_client(is_async=True)
        full_key = f"{input_data.namespace}:{input_data.key}"

        value = await redis.get(full_key)
        ttl = await redis.ttl(full_key) if value else -1

        return ToolOutputSchema(
            success=True,
            message=f"Retrieved value for key '{input_data.key}'"
            if value
            else f"Key '{input_data.key}' not found",
            data={
                "value": value.decode("utf-8") if value else None,
                "found": value is not None,
                "ttl": ttl if ttl > 0 else None,
                "key": input_data.key,
                "namespace": input_data.namespace,
            },
        )

@register_tool
class CacheSetTool(InnerTool):
    """Set a value in the cache"""

    METADATA = ToolMetadata(
        name="cache_set",
        display_name="Cache Set",
        description="Set a value in the Redis cache with TTL",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="utility",
        tags=["cache", "redis", "storage"],
        timeout=5,
    )

    class InputSchema(ToolInputSchema):
        key: str = Field(description="Cache key")
        value: str = Field(description="Value to cache (string)")
        namespace: str = Field(default="default", description="Cache namespace")
        ttl_seconds: int = Field(
            default=3600, ge=1, le=86400 * 7, description="Time-to-live in seconds"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from aiwen.middleware.cache_middleware import get_redis_client

        redis = get_redis_client(is_async=True)
        full_key = f"{input_data.namespace}:{input_data.key}"

        await redis.setex(full_key, input_data.ttl_seconds, input_data.value)

        return ToolOutputSchema(
            success=True,
            message=f"Cached value for key '{input_data.key}' with TTL {input_data.ttl_seconds}s",
            data={
                "key": input_data.key,
                "namespace": input_data.namespace,
                "ttl_seconds": input_data.ttl_seconds,
            },
        )

@register_tool
class GetWorkspaceInfoTool(InnerTool):
    """Get detailed information about a workspace"""

    METADATA = ToolMetadata(
        name="get_workspace_info",
        display_name="Get Workspace Info",
        description="Get detailed information about a workspace including name, owner, settings",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="utility",
        tags=["workspace", "info", "metadata"],
        timeout=10,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="The workspace UUID")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from sqlalchemy import func, select

        from aiwen.extensions.database import get_session
        from aiwen.models.runs.run import Run
        from aiwen.models.workspaces.workspace import Workspace

        async with get_session("aiwen") as db:
            result = await db.execute(
                select(Workspace).where(Workspace.id == input_data.workspace_id)
            )
            workspace = result.scalar_one_or_none()

            if not workspace:
                return ToolOutputSchema(
                    success=False,
                    message=f"Workspace not found: {input_data.workspace_id}",
                    error=f"No workspace found with ID {input_data.workspace_id}",
                )

            # Get run count
            run_count_result = await db.execute(
                select(func.count(Run.id)).where(
                    Run.workspace_id == input_data.workspace_id
                )
            )
            run_count = run_count_result.scalar() or 0

            return ToolOutputSchema(
                success=True,
                message=f"Retrieved workspace info for {workspace.name}",
                data={
                    "id": str(workspace.id),
                    "name": workspace.name,
                    "description": workspace.description,
                    "owner_id": str(workspace.owner_id) if workspace.owner_id else None,
                    "app_id": str(workspace.app_id) if workspace.app_id else None,
                    "settings": workspace.settings or {},
                    "run_count": run_count,
                    "created_at": workspace.created_at.isoformat()
                    if workspace.created_at
                    else None,
                },
            )

@register_tool
class GetRunHistoryTool(InnerTool):
    """Get recent run history for a workspace"""

    METADATA = ToolMetadata(
        name="get_run_history",
        display_name="Get Run History",
        description="Get recent run history for a workspace with optional status filter",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="utility",
        tags=["runs", "history", "workspace"],
        timeout=15,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="The workspace UUID")
        limit: int = Field(
            default=10, ge=1, le=100, description="Maximum runs to return"
        )
        status: str | None = Field(
            default=None,
            description="Filter by status (running, completed, failed, etc.)",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from sqlalchemy import select

        from aiwen.extensions.database import get_session
        from aiwen.models.runs.run import Run

        async with get_session("aiwen") as db:
            query = (
                select(Run)
                .where(Run.workspace_id == input_data.workspace_id)
                .order_by(Run.created_at.desc())
                .limit(input_data.limit)
            )

            if input_data.status:
                query = query.where(Run.status == input_data.status)

            result = await db.execute(query)
            runs = result.scalars().all()

            return ToolOutputSchema(
                success=True,
                message=f"Retrieved {len(runs)} runs for workspace",
                data={
                    "runs": [
                        {
                            "id": str(run.id),
                            "status": run.status,
                            "trigger_type": run.trigger_type,
                            "created_at": run.created_at.isoformat()
                            if run.created_at
                            else None,
                            "completed_at": run.completed_at.isoformat()
                            if run.completed_at
                            else None,
                            "input_preview": str(run.input_data)[:100]
                            if run.input_data
                            else None,
                        }
                        for run in runs
                    ],
                    "total": len(runs),
                    "workspace_id": input_data.workspace_id,
                },
            )


# ═══════════════════════════════════════════════════════════════════════════════
# CONTEXT TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@register_tool
class SearchContextTool(InnerTool):
    """Search context content using regular expressions"""

    METADATA = ToolMetadata(
        name="search_context",
        display_name="Search Context",
        description="Search through context content using regex patterns (grep-like)",
        execution_mode=ToolExecutionMode.SERVER_RUN,
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
        import re

        from sqlalchemy import select

        from aiwen.extensions.database import get_session
        from aiwen.models.context.context import Context

        # Compile regex pattern with flags
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
            # Build query
            query = select(Context)

            # Apply filters
            if input_data.context_id:
                query = query.where(Context.id == input_data.context_id)
            if input_data.user_id:
                query = query.where(Context.user_id == input_data.user_id)
            if input_data.context_type:
                query = query.where(Context.context_type == input_data.context_type)

            # Order by creation time (most recent first)
            query = query.order_by(Context.created_at.desc())

            result = await db.execute(query)
            contexts = result.scalars().all()

            # Search through contexts and collect matches
            matches = []
            total_match_count = 0

            for ctx in contexts:
                if not ctx.content:
                    continue

                # Find all matches in this context
                context_matches = []
                for match in regex.finditer(ctx.content):
                    start, end = match.span()
                    matched_text = match.group(0)

                    # Extract context (chars before and after)
                    context_start = max(0, start - input_data.context_chars)
                    context_end = min(len(ctx.content), end + input_data.context_chars)

                    # Get the text with context
                    before = ctx.content[context_start:start]
                    after = ctx.content[end:context_end]

                    # Add ellipsis if truncated
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

                    # Stop if we've reached max_results
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

@register_tool
class QueryStructuredDataTool(InnerTool):
    """Execute a read-only SQL query on structured data"""

    METADATA = ToolMetadata(
        name="query_structured_data",
        display_name="Query Structured Data",
        description="Execute a read-only SQL query (SELECT only) on database",
        execution_mode=ToolExecutionMode.SERVER_RUN,
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
        # Security: Only allow SELECT queries
        sql_upper = input_data.sql.strip().upper()
        if not sql_upper.startswith("SELECT"):
            return ToolOutputSchema(
                success=False,
                message="Only SELECT queries are allowed",
                error="Query must start with SELECT",
                data={"columns": [], "rows": [], "row_count": 0},
            )

        # Block dangerous keywords
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
            # Add LIMIT if not present
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


@register_tool
class CodeExecutionInnerTool(InnerTool):
    """Execute Python code with input data in a sandboxed context

    This is the built-in execution backend for user-defined SERVER_RUN tools.
    ExternalTool instances delegate to this tool to run user-provided Python code.
    """

    METADATA = ToolMetadata(
        name="code_execution",
        display_name="Code Execution",
        description="Execute Python code with input data in a controlled context",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="execution",
        tags=["code", "python", "execution", "inner"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        code: str = Field(description="Python code to execute")
        input_data: dict = Field(
            default_factory=dict,
            description="Input parameters available to the code as 'input_data' variable",
        )

    async def execute(self, input_data: ToolInputSchema) -> ToolOutputSchema:
        """Execute Python code with input_data in execution context"""
        # Cast input_data to the specific InputSchema type
        typed_input = self.InputSchema.model_validate(input_data.model_dump())

        if not typed_input.code:
            return ToolOutputSchema(
                success=False,
                error="No code provided for execution",
            )

        try:
            # Create execution context
            context: dict[str, Any] = {
                "input_data": typed_input.input_data,
                "__builtins__": __builtins__,
            }

            # Execute code
            exec(typed_input.code, context)

            # Get result
            if "result" in context:
                result = context["result"]
                return ToolOutputSchema(
                    success=True,
                    message="Code executed successfully",
                    data=result if isinstance(result, dict) else {"result": result},
                )
            return ToolOutputSchema(
                success=False,
                error="Code did not produce a 'result' variable",
            )

        except Exception as e:
            logger.error(f"Code execution error: {e}", exc_info=True)
            return ToolOutputSchema(
                success=False,
                error=f"Execution error: {e!s}",
            )

@register_tool
class HttpRequestInnerTool(InnerTool):
    """Make HTTP API calls

    This is the built-in execution backend for user-defined HTTP tools.
    ExternalTool instances delegate to this tool to make HTTP requests.
    """

    METADATA = ToolMetadata(
        name="http_request",
        display_name="HTTP Request",
        description="Make HTTP API calls with configurable method, headers, and body",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="execution",
        tags=["http", "api", "request", "inner"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        url: str = Field(description="Target URL")
        method: str = Field(
            default="POST", description="HTTP method (GET, POST, PUT, DELETE, PATCH)"
        )
        headers: dict[str, str] = Field(
            default_factory=dict, description="Request headers"
        )
        body: dict = Field(default_factory=dict, description="Request body (JSON)")
        timeout: int = Field(
            default=30, ge=1, le=300, description="Request timeout in seconds"
        )
        verify_ssl: bool = Field(
            default=True, description="Whether to verify SSL certificates"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        """Execute HTTP request"""
        import httpx

        try:
            async with httpx.AsyncClient(verify=input_data.verify_ssl) as client:
                request_kwargs: dict[str, Any] = {
                    "method": input_data.method.upper(),
                    "url": input_data.url,
                    "headers": input_data.headers,
                    "timeout": input_data.timeout,
                }

                # Add body for methods that support it
                if (
                    input_data.method.upper() in ("POST", "PUT", "PATCH")
                    and input_data.body
                ):
                    request_kwargs["json"] = input_data.body
                elif input_data.body:
                    request_kwargs["params"] = input_data.body

                response = await client.request(**request_kwargs)

                # Try to parse as JSON, fall back to text
                try:
                    response_data = response.json()
                except Exception:
                    response_data = {"text": response.text}

                return ToolOutputSchema(
                    success=response.is_success,
                    message=f"HTTP {input_data.method.upper()} {input_data.url} -> {response.status_code}",
                    data={
                        "status_code": response.status_code,
                        "headers": dict(response.headers),
                        "body": response_data,
                    },
                )

        except httpx.TimeoutException:
            return ToolOutputSchema(
                success=False,
                error=f"HTTP request timed out after {input_data.timeout}s",
            )
        except Exception as e:
            logger.error(f"HTTP request error: {e}", exc_info=True)
            return ToolOutputSchema(
                success=False,
                error=f"HTTP request failed: {e!s}",
            )

@register_tool
class ClientRequestInnerTool(InnerTool):
    """Execute a request on the user's client (browser)

    This is the built-in execution backend for user-defined CLIENT_RUN tools.
    ExternalTool instances delegate to this tool to send instructions to the
    user's browser, where a registered frontend handler picks up and executes
    the request. The server packages the payload and returns it via WebSocket
    or SSE so the client can act on it.
    """

    METADATA = ToolMetadata(
        name="client_request",
        display_name="Client Request",
        description="Send a request to be executed on the user's client (browser-side handler)",
        execution_mode=ToolExecutionMode.CLIENT_RUN,
        category="execution",
        tags=["client", "browser", "request", "inner"],
        timeout=120,
        client_config=ClientConfig(
            handler_name="clientRequest",
            config={},
            require_user_approval=True,
        ),
    )

    class InputSchema(ToolInputSchema):
        handler_name: str = Field(
            description="Name of the frontend handler to invoke (e.g., 'filePicker', 'clipboard', 'notification')",
        )
        action: str = Field(
            description="Action for the handler to perform (e.g., 'read', 'write', 'show')",
        )
        params: dict = Field(
            default_factory=dict,
            description="Parameters to pass to the client handler",
        )
        require_approval: bool = Field(
            default=True,
            description="Whether user approval is required before execution",
        )
        timeout_ms: int = Field(
            default=60000,
            ge=1000,
            le=300000,
            description="Client-side execution timeout in milliseconds",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        """Package the client request payload for delivery to the frontend.

        The actual execution happens on the client side. This method builds
        the instruction payload that will be sent over the WebSocket / SSE
        channel. The client framework dispatches it to the registered handler
        and streams the result back.
        """
        if not input_data.handler_name:
            return ToolOutputSchema(
                success=False,
                error="No handler_name provided for client request",
            )

        payload = {
            "type": "client_request",
            "handler_name": input_data.handler_name,
            "action": input_data.action,
            "params": input_data.params,
            "require_approval": input_data.require_approval,
            "timeout_ms": input_data.timeout_ms,
        }

        logger.info(
            f"Client request prepared: handler={input_data.handler_name}, "
            f"action={input_data.action}"
        )

        return ToolOutputSchema(
            success=True,
            message=f"Client request dispatched to handler '{input_data.handler_name}'",
            data=payload,
        )

@register_tool
class SandboxExecutionInnerTool(InnerTool):
    """Execute a command in an isolated sandbox (Docker container)

    This is the built-in execution backend for user-defined CONTAINER_RUN tools.
    ExternalTool instances delegate to this tool to run arbitrary shell commands
    inside a short-lived Docker container with configurable resource limits.
    """

    METADATA = ToolMetadata(
        name="sandbox_execution",
        display_name="Sandbox Execution",
        description="Execute a shell command in an isolated Docker sandbox with resource limits",
        execution_mode=ToolExecutionMode.CONTAINER_RUN,
        category="execution",
        tags=["sandbox", "container", "docker", "shell", "inner"],
        timeout=120,
        container_config=ContainerConfig(
            image="python:3.12-slim",
            workdir="/workspace",
            resource_limits=ResourceLimits(
                memory="256m",
                cpu_quota=50000,
                cpu_period=100000,
                network_enabled=False,
                read_only_rootfs=True,
                pids_limit=100,
            ),
            environment={"PYTHONUNBUFFERED": "1"},
        ),
    )

    class InputSchema(ToolInputSchema):
        command: str = Field(
            description="Shell command to execute inside the sandbox",
        )
        image: str = Field(
            default="python:3.12-slim",
            description="Docker image to use for the sandbox",
        )
        timeout_seconds: int = Field(
            default=60,
            ge=1,
            le=600,
            description="Execution timeout in seconds",
        )
        memory_limit: str = Field(
            default="256m",
            description="Memory limit (e.g., '256m', '1g')",
        )
        network_enabled: bool = Field(
            default=False,
            description="Whether to allow network access inside the sandbox",
        )
        workdir: str = Field(
            default="/workspace",
            description="Working directory inside the container",
        )
        environment: dict[str, str] = Field(
            default_factory=dict,
            description="Additional environment variables for the container",
        )
        stdin_data: str | None = Field(
            default=None,
            description="Optional data to pass via stdin",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        """Execute a command inside a Docker container sandbox.

        Spins up a short-lived container with the specified resource limits,
        runs the command, captures stdout/stderr and the exit code, then
        tears down the container.
        """
        import asyncio

        if not input_data.command:
            return ToolOutputSchema(
                success=False,
                error="No command provided for sandbox execution",
            )

        logger.info(
            f"Sandbox execution: image={input_data.image}, "
            f"command={input_data.command[:100]}..."
        )

        try:
            import docker
            from docker.errors import ContainerError, ImageNotFound

            client = docker.from_env()

            # Build resource limit kwargs
            container_kwargs: dict[str, Any] = {
                "image": input_data.image,
                "command": ["sh", "-c", input_data.command],
                "working_dir": input_data.workdir,
                "mem_limit": input_data.memory_limit,
                "pids_limit": 100,
                "network_disabled": not input_data.network_enabled,
                "read_only": True,
                "detach": True,
                "stdout": True,
                "stderr": True,
                "environment": {
                    "PYTHONUNBUFFERED": "1",
                    **input_data.environment,
                },
                # tmpfs so writable /tmp is available even with read_only rootfs
                "tmpfs": {"/tmp": "size=64m"},
            }

            if input_data.stdin_data:
                container_kwargs["stdin_open"] = True

            container = client.containers.run(**container_kwargs)

            try:
                # Wait for the container to finish with a timeout
                result = await asyncio.to_thread(
                    container.wait, timeout=input_data.timeout_seconds
                )
                exit_code = result.get("StatusCode", -1)

                stdout = (
                    await asyncio.to_thread(container.logs, stdout=True, stderr=False)
                ).decode("utf-8", errors="replace")
                stderr = (
                    await asyncio.to_thread(container.logs, stdout=False, stderr=True)
                ).decode("utf-8", errors="replace")
            finally:
                # Always clean up the container
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(container.remove, force=True)

            return ToolOutputSchema(
                success=exit_code == 0,
                message=f"Command exited with code {exit_code}",
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": exit_code,
                    "image": input_data.image,
                    "command": input_data.command,
                },
            )

        except ImageNotFound:
            return ToolOutputSchema(
                success=False,
                error=f"Docker image not found: {input_data.image}",
            )
        except ContainerError as e:
            return ToolOutputSchema(
                success=False,
                error=f"Container error: {e!s}",
                data={
                    "exit_code": e.exit_status,
                    "stderr": e.stderr.decode("utf-8", errors="replace")
                    if e.stderr
                    else "",
                },
            )
        except Exception as e:
            logger.error(f"Sandbox execution error: {e}", exc_info=True)
            return ToolOutputSchema(
                success=False,
                error=f"Sandbox execution failed: {e!s}",
            )