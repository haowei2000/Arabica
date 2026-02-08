"""
Server-side tools for fast, trusted operations.

These tools execute directly in the API server process, suitable for:
- Database/context queries
- File system browsing
- Knowledge base search
- Cache operations
- Internal API calls

Server tools are the default execution mode - fast with no isolation overhead.
"""

from datetime import UTC, datetime
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.tools import tool

from aiwen.services.executor.tools.execution_mode import server_tool

# ═══════════════════════════════════════════════════════════════════════════════
# CONTEXT & KNOWLEDGE TOOLS
# ═══════════════════════════════════════════════════════════════════════════════


@tool("search_in_context")
@server_tool(timeout=30)
async def search_context(
    pattern: str,
    context_id: str | None = None,
    user_id: str | None = None,
    context_type: str | None = None,
    max_results: int = 10,
    context_chars: int = 100,
    ignore_case: bool = True,
    multiline: bool = False,
) -> dict:
    """
    Search context content using regular expressions (grep-like).

    This tool searches through context content using regex patterns,
    similar to the grep command. It returns matching text fragments
    with surrounding context.

    Args:
        pattern: Regular expression pattern to search for
        context_id: Optional specific context ID to search in
        user_id: Optional user ID to scope the search
        context_type: Optional context type filter (conversation, tool, knowledge)
        max_results: Maximum number of matching contexts to return (default: 10)
        context_chars: Number of characters to show before/after match (default: 100)
        ignore_case: Whether to ignore case in pattern matching (default: True)
        multiline: Whether to enable multiline mode (default: False)

    Returns:
        dict with:
        - matches: List of matching fragments with metadata
        - total_matches: Total number of matching contexts found
        - pattern: The regex pattern used
        - stats: Search statistics

    Example:
        # Search for email addresses
        search_context(pattern=r'\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}\\b')

        # Search for specific keywords (case-insensitive)
        search_context(pattern='error|warning|fail', ignore_case=True)

        # Find code blocks
        search_context(pattern=r'```[\\s\\S]*?```', multiline=True)
    """
    import re

    from sqlalchemy import select

    from aiwen.extensions.database import get_session
    from aiwen.models.context.context import Context

    # Compile regex pattern with flags
    flags = 0
    if ignore_case:
        flags |= re.IGNORECASE
    if multiline:
        flags |= re.MULTILINE | re.DOTALL

    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return {
            "error": f"Invalid regex pattern: {e}",
            "matches": [],
            "total_matches": 0,
            "pattern": pattern,
        }

    async with get_session("aiwen") as db:
        # Build query
        query = select(Context)

        # Apply filters
        if context_id:
            query = query.where(Context.id == context_id)
        if user_id:
            query = query.where(Context.user_id == user_id)
        if context_type:
            query = query.where(Context.context_type == context_type)

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
                context_start = max(0, start - context_chars)
                context_end = min(len(ctx.content), end + context_chars)

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
                        # Calculate approximate line number
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
                if len(matches) >= max_results:
                    break

        return {
            "matches": matches,
            "total_contexts_matched": len(matches),
            "total_pattern_matches": total_match_count,
            "contexts_searched": len(contexts),
            "pattern": pattern,
            "options": {
                "ignore_case": ignore_case,
                "multiline": multiline,
                "context_chars": context_chars,
            },
        }


@tool("get_run_memory")
@server_tool(timeout=15)
async def get_run_memory(
    run_id: str,
    max_messages: int = 20,
    include_system: bool = False,
) -> dict:
    """
    Retrieve conversation history for context.

    Args:
        run_id: The conversation UUID
        max_messages: Maximum number of messages to retrieve
        include_system: Whether to include system messages

    Returns:
        dict with:
        - messages: List of messages with role and content
        - total: Total message count
    """
    from aiwen.services.executor.executor_template.default.context import (
        get_messages_from_context,
    )

    messages = await get_messages_from_context(
        conversation_id=UUID(run_id),
        max_messages=max_messages,
    )

    if not include_system:
        messages = [m for m in messages if m.get("role") != "system"]

    return {
        "messages": messages,
        "total": len(messages),
        "conversation_id": run_id,
    }


@tool("query_structured_data")
@server_tool(timeout=60)
async def query_structured_data(
    sql: str,
    database: str = "aiwen",
    params: dict[str, Any] | None = None,
    max_rows: int = 100,
) -> dict:
    """
    Execute a read-only SQL query on structured data.

    Args:
        sql: SQL SELECT query (only SELECT allowed)
        database: Database name ("aiwen", "mes", etc.)
        params: Query parameters for safe binding
        max_rows: Maximum rows to return

    Returns:
        dict with:
        - columns: List of column names
        - rows: List of row data
        - row_count: Number of rows returned
    """
    # Security: Only allow SELECT queries
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT"):
        return {
            "error": "Only SELECT queries are allowed",
            "columns": [],
            "rows": [],
            "row_count": 0,
        }

    # Block dangerous keywords
    dangerous = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE", "EXEC"]
    for keyword in dangerous:
        if keyword in sql_upper:
            return {
                "error": f"Query contains forbidden keyword: {keyword}",
                "columns": [],
                "rows": [],
                "row_count": 0,
            }

    from sqlalchemy import text

    from aiwen.extensions.database import get_session

    async with get_session(database) as db:
        # Add LIMIT if not present
        if "LIMIT" not in sql_upper:
            sql = f"{sql} LIMIT {max_rows}"

        result = await db.execute(text(sql), params or {})
        rows = result.fetchall()
        columns = list(result.keys()) if rows else []

        return {
            "columns": columns,
            "rows": [list(row) for row in rows],
            "row_count": len(rows),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# FILE SYSTEM TOOLS
# ═══════════════════════════════════════════════════════════════════════════════


@tool("list_workspace_files")
@server_tool(timeout=10)
async def list_workspace_files(
    workspace_id: str,
    path: str = "/",
    recursive: bool = False,
    file_types: list[str] | None = None,
) -> dict:
    """
    List files in a workspace directory.

    Args:
        workspace_id: The workspace UUID
        path: Relative path within workspace (default: root)
        recursive: Whether to list subdirectories recursively
        file_types: Filter by file extensions (e.g., [".pdf", ".txt"])

    Returns:
        dict with:
        - files: List of file info (name, size, modified, type)
        - directories: List of subdirectory names
        - total_files: Total file count
    """
    from sqlalchemy import select

    from aiwen.extensions.database import get_session

    async with get_session("aiwen") as db:
        # Query from document storage
        from aiwen.models.context.document import Document

        query = select(Document).where(Document.workspace_id == workspace_id)

        if path != "/":
            query = query.where(Document.path.like(f"{path}%"))

        if file_types:
            # Filter by extension
            from sqlalchemy import or_

            ext_filters = [Document.name.like(f"%{ext}") for ext in file_types]
            query = query.where(or_(*ext_filters))

        result = await db.execute(query)
        documents = result.scalars().all()

        files = []
        directories = set()

        for doc in documents:
            doc_path = doc.path or "/"
            relative_path = (
                doc_path[len(path) :] if doc_path.startswith(path) else doc_path
            )

            # Check if it's in a subdirectory
            if "/" in relative_path.strip("/"):
                if not recursive:
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

        return {
            "files": files,
            "directories": list(directories),
            "total_files": len(files),
            "path": path,
        }


@tool("read_file_content")
@server_tool(timeout=30)
async def read_file_content(
    file_id: str,
    max_length: int = 10000,
    encoding: str = "utf-8",
) -> dict:
    """
    Read the content of a file from storage.

    Args:
        file_id: The file/document UUID
        max_length: Maximum characters to return
        encoding: Text encoding (default: utf-8)

    Returns:
        dict with:
        - content: File content (truncated if needed)
        - truncated: Whether content was truncated
        - total_length: Original content length
        - metadata: File metadata
    """
    from sqlalchemy import select

    from aiwen.extensions.database import get_session

    async with get_session("aiwen") as db:
        from aiwen.models.context.document import Document

        result = await db.execute(select(Document).where(Document.id == file_id))
        doc = result.scalar_one_or_none()

        if not doc:
            return {
                "error": f"File not found: {file_id}",
                "content": None,
                "truncated": False,
                "total_length": 0,
            }

        # Get content from storage
        content = doc.content or ""
        total_length = len(content)
        truncated = total_length > max_length

        return {
            "content": content[:max_length],
            "truncated": truncated,
            "total_length": total_length,
            "metadata": {
                "name": doc.name,
                "type": doc.content_type,
                "size": doc.size,
                "path": doc.path,
            },
        }


@tool("search_files")
@server_tool(timeout=20)
async def search_files(
    query: str,
    workspace_id: str | None = None,
    file_types: list[str] | None = None,
    max_results: int = 20,
) -> dict:
    """
    Search files by name or content.

    Args:
        query: Search query
        workspace_id: Optional workspace to scope search
        file_types: Filter by extensions
        max_results: Maximum results to return

    Returns:
        dict with:
        - results: List of matching files with relevance info
        - total: Total matches found
    """
    from sqlalchemy import or_, select

    from aiwen.extensions.database import get_session

    async with get_session("aiwen") as db:
        from aiwen.models.context.document import Document

        base_query = select(Document)

        if workspace_id:
            base_query = base_query.where(Document.workspace_id == workspace_id)

        # Search in name and content
        search_filter = or_(
            Document.name.ilike(f"%{query}%"),
            Document.content.ilike(f"%{query}%"),
        )
        base_query = base_query.where(search_filter)

        if file_types:
            ext_filters = [Document.name.like(f"%{ext}") for ext in file_types]
            base_query = base_query.where(or_(*ext_filters))

        base_query = base_query.limit(max_results)

        result = await db.execute(base_query)
        documents = result.scalars().all()

        return {
            "results": [
                {
                    "id": str(doc.id),
                    "name": doc.name,
                    "path": doc.path,
                    "type": doc.content_type,
                    "size": doc.size,
                    "match_in_name": query.lower() in (doc.name or "").lower(),
                    "match_in_content": query.lower() in (doc.content or "").lower(),
                }
                for doc in documents
            ],
            "total": len(documents),
            "query": query,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY TOOLS
# ═══════════════════════════════════════════════════════════════════════════════


@tool("get_current_time")
@server_tool(timeout=5)
async def get_current_time(
    timezone: str = "UTC",
    format: str = "%Y-%m-%d %H:%M:%S",
) -> dict:
    """
    Get the current server time.

    Args:
        timezone: Timezone name (e.g., "UTC", "Asia/Shanghai")
        format: Output format string

    Returns:
        dict with:
        - time: Formatted time string
        - timestamp: Unix timestamp
        - timezone: Timezone used
    """
    from datetime import timezone as tz
    from zoneinfo import ZoneInfo

    try:
        zone = ZoneInfo(timezone)
    except Exception:
        zone = UTC
        timezone = "UTC"

    now = datetime.now(zone)

    return {
        "time": now.strftime(format),
        "timestamp": int(now.timestamp()),
        "timezone": timezone,
        "iso": now.isoformat(),
    }


@tool("get_workspace_info")
@server_tool(timeout=10)
async def get_workspace_info(workspace_id: str) -> dict:
    """
    Get detailed information about a workspace.

    Args:
        workspace_id: The workspace UUID

    Returns:
        dict with workspace details including name, owner, settings
    """
    from sqlalchemy import func, select

    from aiwen.extensions.database import get_session

    async with get_session("aiwen") as db:
        from aiwen.models.workspaces.workspace import Workspace

        result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
        workspace = result.scalar_one_or_none()

        if not workspace:
            return {"error": f"Workspace not found: {workspace_id}"}

        # Get run count
        from aiwen.models.runs.run import Run

        run_count_result = await db.execute(
            select(func.count(Run.id)).where(Run.workspace_id == workspace_id)
        )
        run_count = run_count_result.scalar() or 0

        return {
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
        }


@tool("get_run_history")
@server_tool(timeout=15)
async def get_run_history(
    workspace_id: str,
    limit: int = 10,
    status: str | None = None,
) -> dict:
    """
    Get recent run history for a workspace.

    Args:
        workspace_id: The workspace UUID
        limit: Maximum runs to return
        status: Filter by status (running, completed, failed, etc.)

    Returns:
        dict with:
        - runs: List of run summaries
        - total: Total count
    """
    from sqlalchemy import select

    from aiwen.extensions.database import get_session

    async with get_session("aiwen") as db:
        from aiwen.models.runs.run import Run

        query = (
            select(Run)
            .where(Run.workspace_id == workspace_id)
            .order_by(Run.created_at.desc())
            .limit(limit)
        )

        if status:
            query = query.where(Run.status == status)

        result = await db.execute(query)
        runs = result.scalars().all()

        return {
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
            "workspace_id": workspace_id,
        }


@tool("cache_get")
@server_tool(timeout=5)
async def cache_get(key: str, namespace: str = "default") -> dict:
    """
    Get a value from the cache.

    Args:
        key: Cache key
        namespace: Cache namespace for isolation

    Returns:
        dict with:
        - value: Cached value (or None)
        - found: Whether key was found
        - ttl: Remaining TTL in seconds (if available)
    """
    from aiwen.middleware.cache_middleware import get_redis_client

    redis = get_redis_client(is_async=True)
    full_key = f"{namespace}:{key}"

    value = await redis.get(full_key)
    ttl = await redis.ttl(full_key) if value else -1

    return {
        "value": value.decode("utf-8") if value else None,
        "found": value is not None,
        "ttl": ttl if ttl > 0 else None,
        "key": key,
        "namespace": namespace,
    }


@tool("cache_set")
@server_tool(timeout=5)
async def cache_set(
    key: str,
    value: str,
    namespace: str = "default",
    ttl_seconds: int = 3600,
) -> dict:
    """
    Set a value in the cache.

    Args:
        key: Cache key
        value: Value to cache (string)
        namespace: Cache namespace
        ttl_seconds: Time-to-live in seconds

    Returns:
        dict with:
        - success: Whether the operation succeeded
        - key: Full cache key used
    """
    from aiwen.middleware.cache_middleware import get_redis_client

    redis = get_redis_client(is_async=True)
    full_key = f"{namespace}:{key}"

    await redis.setex(full_key, ttl_seconds, value)

    return {
        "success": True,
        "key": key,
        "namespace": namespace,
        "ttl_seconds": ttl_seconds,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Tool Collections
# ═══════════════════════════════════════════════════════════════════════════════

CONTEXT_TOOLS = [
    search_context,
    get_run_memory,
    query_structured_data,
]

FILE_TOOLS = [
    list_workspace_files,
    read_file_content,
    search_files,
]

UTILITY_TOOLS = [
    get_current_time,
    get_workspace_info,
    get_run_history,
    cache_get,
    cache_set,
]

# All server tools combined
SERVER_TOOLS = CONTEXT_TOOLS + FILE_TOOLS + UTILITY_TOOLS
