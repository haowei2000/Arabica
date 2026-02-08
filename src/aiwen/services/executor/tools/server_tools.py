"""
Server-side tools using BaseTool class system

Migrated from LangChain @tool decorator to unified BaseTool interface.
All tools execute directly in the API server process.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from aiwen.services.executor.tools.base_tool import (
    BaseTool,
    ToolExecutionMode,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)

# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY TOOLS
# ═══════════════════════════════════════════════════════════════════════════════


class GetCurrentTimeTool(BaseTool):
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


class CacheGetTool(BaseTool):
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
        namespace: str = Field(default="default", description="Cache namespace for isolation")

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


class CacheSetTool(BaseTool):
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


class GetWorkspaceInfoTool(BaseTool):
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
                select(func.count(Run.id)).where(Run.workspace_id == input_data.workspace_id)
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


class GetRunHistoryTool(BaseTool):
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
        limit: int = Field(default=10, ge=1, le=100, description="Maximum runs to return")
        status: str | None = Field(
            default=None, description="Filter by status (running, completed, failed, etc.)"
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


class SearchContextTool(BaseTool):
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
        user_id: str | None = Field(default=None, description="Optional user ID to scope the search")
        context_type: str | None = Field(
            default=None, description="Optional context type filter (conversation, tool, knowledge)"
        )
        max_results: int = Field(
            default=10, ge=1, le=100, description="Maximum number of matching contexts to return"
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
        multiline: bool = Field(default=False, description="Whether to enable multiline mode")

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
                            "created_at": ctx.created_at.isoformat() if ctx.created_at else None,
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


class GetRunMemoryTool(BaseTool):
    """Retrieve conversation history for context"""

    METADATA = ToolMetadata(
        name="get_run_memory",
        display_name="Get Run Memory",
        description="Retrieve conversation history for a run to provide context",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="context",
        tags=["memory", "conversation", "history", "context"],
        timeout=15,
    )

    class InputSchema(ToolInputSchema):
        run_id: str = Field(description="The conversation UUID")
        max_messages: int = Field(
            default=20, ge=1, le=100, description="Maximum number of messages to retrieve"
        )
        include_system: bool = Field(
            default=False, description="Whether to include system messages"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from aiwen.services.executor.executor_template.default.context import (
            get_messages_from_context,
        )

        messages = await get_messages_from_context(
            conversation_id=UUID(input_data.run_id),
            max_messages=input_data.max_messages,
        )

        if not input_data.include_system:
            messages = [m for m in messages if m.get("role") != "system"]

        return ToolOutputSchema(
            success=True,
            message=f"Retrieved {len(messages)} messages",
            data={
                "messages": messages,
                "total": len(messages),
                "conversation_id": input_data.run_id,
            },
        )


class QueryStructuredDataTool(BaseTool):
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


# ═══════════════════════════════════════════════════════════════════════════════
# FILE TOOLS
# ═══════════════════════════════════════════════════════════════════════════════


class ListWorkspaceFilesTool(BaseTool):
    """List files in a workspace directory"""

    METADATA = ToolMetadata(
        name="list_workspace_files",
        display_name="List Workspace Files",
        description="List files in a workspace directory with optional filtering",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="file",
        tags=["files", "workspace", "list", "directory"],
        timeout=10,
    )

    class InputSchema(ToolInputSchema):
        workspace_id: str = Field(description="The workspace UUID")
        path: str = Field(default="/", description="Relative path within workspace (default: root)")
        recursive: bool = Field(
            default=False, description="Whether to list subdirectories recursively"
        )
        file_types: list[str] | None = Field(
            default=None, description='Filter by file extensions (e.g., [".pdf", ".txt"])'
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from sqlalchemy import or_, select

        from aiwen.extensions.database import get_session
        from aiwen.models.context.document import Document

        async with get_session("aiwen") as db:
            query = select(Document).where(Document.workspace_id == input_data.workspace_id)

            if input_data.path != "/":
                query = query.where(Document.path.like(f"{input_data.path}%"))

            if input_data.file_types:
                # Filter by extension
                ext_filters = [
                    Document.name.like(f"%{ext}") for ext in input_data.file_types
                ]
                query = query.where(or_(*ext_filters))

            result = await db.execute(query)
            documents = result.scalars().all()

            files = []
            directories = set()

            for doc in documents:
                doc_path = doc.path or "/"
                relative_path = (
                    doc_path[len(input_data.path) :]
                    if doc_path.startswith(input_data.path)
                    else doc_path
                )

                # Check if it's in a subdirectory
                if "/" in relative_path.strip("/"):
                    if not input_data.recursive:
                        # Just record the immediate subdirectory
                        subdir = relative_path.strip("/").split("/")[0]
                        directories.add(subdir)
                        continue

                files.append(
                    {
                        "name": doc.name,
                        "path": doc.path,
                        "size": doc.size,
                        "type": doc.content_type,
                        "modified": doc.updated_at.isoformat() if doc.updated_at else None,
                    }
                )

            return ToolOutputSchema(
                success=True,
                message=f"Found {len(files)} files in workspace",
                data={
                    "files": files,
                    "directories": list(directories),
                    "total_files": len(files),
                    "path": input_data.path,
                },
            )


class ReadFileContentTool(BaseTool):
    """Read the content of a file from storage"""

    METADATA = ToolMetadata(
        name="read_file_content",
        display_name="Read File Content",
        description="Read the content of a file from storage with optional truncation",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="file",
        tags=["files", "read", "content", "document"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        file_id: str = Field(description="The file/document UUID")
        max_length: int = Field(
            default=10000, ge=1, le=100000, description="Maximum characters to return"
        )
        encoding: str = Field(default="utf-8", description="Text encoding")

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from sqlalchemy import select

        from aiwen.extensions.database import get_session
        from aiwen.models.context.document import Document

        async with get_session("aiwen") as db:
            result = await db.execute(select(Document).where(Document.id == input_data.file_id))
            doc = result.scalar_one_or_none()

            if not doc:
                return ToolOutputSchema(
                    success=False,
                    message=f"File not found: {input_data.file_id}",
                    error=f"No document found with ID {input_data.file_id}",
                    data={
                        "content": None,
                        "truncated": False,
                        "total_length": 0,
                    },
                )

            # Get content from storage
            content = doc.content or ""
            total_length = len(content)
            truncated = total_length > input_data.max_length

            return ToolOutputSchema(
                success=True,
                message=f"Read file {doc.name}" + (" (truncated)" if truncated else ""),
                data={
                    "content": content[: input_data.max_length],
                    "truncated": truncated,
                    "total_length": total_length,
                    "metadata": {
                        "name": doc.name,
                        "type": doc.content_type,
                        "size": doc.size,
                        "path": doc.path,
                    },
                },
            )


class SearchFilesTool(BaseTool):
    """Search files by name or content"""

    METADATA = ToolMetadata(
        name="search_files",
        display_name="Search Files",
        description="Search files by name or content with optional filtering",
        execution_mode=ToolExecutionMode.SERVER_RUN,
        category="file",
        tags=["files", "search", "find", "documents"],
        timeout=20,
    )

    class InputSchema(ToolInputSchema):
        query: str = Field(description="Search query")
        workspace_id: str | None = Field(
            default=None, description="Optional workspace to scope search"
        )
        file_types: list[str] | None = Field(
            default=None, description="Filter by extensions"
        )
        max_results: int = Field(
            default=20, ge=1, le=100, description="Maximum results to return"
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        from sqlalchemy import or_, select

        from aiwen.extensions.database import get_session
        from aiwen.models.context.document import Document

        async with get_session("aiwen") as db:
            base_query = select(Document)

            if input_data.workspace_id:
                base_query = base_query.where(Document.workspace_id == input_data.workspace_id)

            # Search in name and content
            search_filter = or_(
                Document.name.ilike(f"%{input_data.query}%"),
                Document.content.ilike(f"%{input_data.query}%"),
            )
            base_query = base_query.where(search_filter)

            if input_data.file_types:
                ext_filters = [Document.name.like(f"%{ext}") for ext in input_data.file_types]
                base_query = base_query.where(or_(*ext_filters))

            base_query = base_query.limit(input_data.max_results)

            result = await db.execute(base_query)
            documents = result.scalars().all()

            return ToolOutputSchema(
                success=True,
                message=f"Found {len(documents)} matching files",
                data={
                    "results": [
                        {
                            "id": str(doc.id),
                            "name": doc.name,
                            "path": doc.path,
                            "type": doc.content_type,
                            "size": doc.size,
                            "match_in_name": input_data.query.lower()
                            in (doc.name or "").lower(),
                            "match_in_content": input_data.query.lower()
                            in (doc.content or "").lower(),
                        }
                        for doc in documents
                    ],
                    "total": len(documents),
                    "query": input_data.query,
                },
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Tool Collections (Class references, not instances)
# ═══════════════════════════════════════════════════════════════════════════════

UTILITY_TOOLS = [
    GetCurrentTimeTool,
    GetWorkspaceInfoTool,
    GetRunHistoryTool,
    CacheGetTool,
    CacheSetTool,
]

CONTEXT_TOOLS = [
    SearchContextTool,
    GetRunMemoryTool,
    QueryStructuredDataTool,
]

FILE_TOOLS = [
    ListWorkspaceFilesTool,
    ReadFileContentTool,
    SearchFilesTool,
]

# All server tools combined
SERVER_TOOLS = CONTEXT_TOOLS + FILE_TOOLS + UTILITY_TOOLS
