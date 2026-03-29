# ContextLayer Framework Integration

## Overview

The `Context` and `WorkspaceContext` models have been updated to align with the ContextLayer framework, which implements progressive disclosure through three layers:

1. **Glance** (Layer 1) - One-line summary for quick scanning
2. **Overview** (Layer 2) - Structured summary with key information
3. **Detail** (Layer 3) - Full content and metadata

## Model Changes

### Context Model

#### New Fields

```python
# Layer 1: Glance - quick scan
glance: str | None  # Max 512 chars, one-line summary

# Layer 2: Overview - structured summary
summary: str | None  # Text field for overview

# Layer 3: Detail - full content
content: str  # Complete content (existing field)

# Tags for filtering
tags: list[str] | None  # Replaces keywords (keywords kept for compatibility)
```

#### New Methods

```python
# Progressive disclosure
context.disclose(level="glance")    # Returns minimal info
context.disclose(level="overview")  # Returns summary + tags
context.disclose(level="detail")    # Returns full content + metadata

# Path operations
context.get_path_depth()            # Number of path segments
context.get_parent_path()           # Parent path string
context.matches_prefix(prefix)      # Check if path starts with prefix

# Tag operations
context.has_tag(tag)                # Check single tag
context.has_any_tag([tag1, tag2])   # Check if any tag exists
context.has_all_tags([tag1, tag2])  # Check if all tags exist
```

#### New Indexes

```sql
CREATE INDEX ix_context_glance ON context (glance);
CREATE INDEX ix_context_user_path ON context (user_id, path);
```

### WorkspaceContext Model

#### New Fields

```python
# Progressive disclosure layers
glance: str | None     # One-line summary
summary: str | None    # Overview summary
content: str | None    # Full content (existing)

# Tags
tags: list[str] | None  # For categorization and filtering
```

#### New Methods

```python
# Progressive disclosure
ws_ctx.disclose(level="glance")
ws_ctx.disclose(level="overview")
ws_ctx.disclose(level="detail")

# Path operations (same as Context)
ws_ctx.get_path_depth()
ws_ctx.get_parent_path()
ws_ctx.matches_prefix(prefix)

# Tag operations (same as Context)
ws_ctx.has_tag(tag)
ws_ctx.has_any_tag(tags)
ws_ctx.has_all_tags(tags)

# Content checks
ws_ctx.has_s3_content     # Property: check S3 storage
ws_ctx.has_inline_content # Property: check inline content
ws_ctx.is_expired         # Property: check expiration
```

#### New Indexes

```sql
CREATE INDEX ix_ws_ctx_glance ON workspace_context (glance);
CREATE INDEX ix_ws_ctx_workspace_deleted ON workspace_context (workspace_id, is_deleted);
```

## Schema Changes

### ContextCreate

```python
from structure.schemas.context import ContextCreate

context = ContextCreate(
    context_type="knowledge",
    path="/workspace_123/knowledge/docs",
    glance="API Documentation — Complete REST API reference",
    summary="Full REST API documentation with examples and schemas",
    content="...",  # Full content here
    tags=["documentation", "api", "rest"],
    importance=90
)
```

### ContextResponse

```python
from structure.schemas.context import ContextResponse

# Response now includes progressive disclosure
response = ContextResponse(
    id="...",
    user_id="...",
    path="/workspace_123/knowledge/docs",
    glance="API Documentation — Complete REST API reference",
    summary="Full REST API documentation...",
    content="...",
    tags=["documentation", "api", "rest"],
    # ... other fields
)

# Use progressive disclosure
glance_view = response.disclose("glance")
# {"path": "/workspace_123/knowledge/docs",
#  "glance": "API Documentation — Complete REST API reference"}

overview_view = response.disclose("overview")
# {"path": "/workspace_123/knowledge/docs",
#  "glance": "API Documentation...",
#  "overview": "Full REST API documentation...",
#  "tags": ["documentation", "api", "rest"]}

detail_view = response.disclose("detail")
# Full content + all metadata
```

## Usage Examples

### Creating Contexts with Progressive Disclosure

```python
from structure.models.context.context import Context
from structure.core.enums import ContextType

# Create a tool context
tool_ctx = Context(
    user_id=user_id,
    path="/workspace_123/tools/web_search",
    context_type=ContextType.TOOL,
    glance="Web Search Tool — Search the web using DuckDuckGo",
    summary="Performs web searches and returns top 10 results with titles and snippets",
    content=json.dumps({
        "name": "web_search",
        "parameters": {...},
        "examples": [...]
    }),
    tags=["tool", "search", "web"],
    importance=80
)

# Create a knowledge context
knowledge_ctx = Context(
    user_id=user_id,
    path="/workspace_123/knowledge/python_guide",
    context_type=ContextType.KNOWLEDGE,
    glance="Python Best Practices — Modern Python development guide",
    summary="Comprehensive guide covering PEP 8, type hints, async/await, and testing",
    content="...",  # Full markdown content
    tags=["python", "best-practices", "guide"],
    importance=90
)
```

### Querying with Path Prefixes

```python
from sqlalchemy import select
from structure.models.context.context import Context

# Get all contexts under a workspace
stmt = select(Context).where(
    Context.path.like("/workspace_123/%")
).order_by(Context.path)

# Get all tools in a workspace
stmt = select(Context).where(
    Context.path.like("/workspace_123/tools/%"),
    Context.context_type == ContextType.TOOL
)

# Get children of a specific path (one level deep)
parent_path = "/workspace_123/knowledge"
stmt = select(Context).where(
    Context.path.like(f"{parent_path}/%"),
    ~Context.path.like(f"{parent_path}/%/%")  # No grandchildren
)
```

### Filtering by Tags

```python
# Get all contexts with a specific tag
stmt = select(Context).where(
    Context.tags.contains(["documentation"])
)

# Get contexts with any of the tags
stmt = select(Context).where(
    Context.tags.op("&&")(["python", "javascript", "rust"])
)

# Use model methods for tag checking
contexts = session.execute(stmt).scalars().all()
python_docs = [c for c in contexts if c.has_all_tags(["python", "documentation"])]
```

### Progressive Disclosure in Queries

```python
# Quick scan - get only glance info
stmt = select(
    Context.path,
    Context.glance
).where(
    Context.path.like("/workspace_123/%")
).order_by(Context.path)

results = session.execute(stmt).all()
for path, glance in results:
    print(f"{path} → {glance}")

# Overview level - more detail
contexts = session.execute(
    select(Context).where(Context.path.like("/workspace_123/%"))
).scalars().all()

for ctx in contexts:
    overview = ctx.disclose("overview")
    print(f"{overview['path']}: {overview['glance']}")
    if 'tags' in overview:
        print(f"  Tags: {', '.join(overview['tags'])}")
```

### WorkspaceContext Examples

```python
from structure.models.context.workspace_context import WorkspaceContext

# Create a temporary file context
file_ctx = WorkspaceContext(
    workspace_id=workspace_id,
    created_by=user_id,
    path="/uploads/report.pdf",
    name="Quarterly Report Q4 2025",
    content_type="application/pdf",
    glance="Q4 2025 Report — Financial summary and projections",
    summary="Quarterly financial report with revenue breakdown and 2026 projections",
    s3_key="workspace_123/uploads/report_q4_2025.pdf",
    size_bytes=2457600,
    tags=["report", "financial", "q4-2025"],
)

# Check content location
if file_ctx.has_s3_content:
    print(f"Content stored in S3: {file_ctx.s3_key}")
elif file_ctx.has_inline_content:
    print("Content stored inline")

# Check expiration
if file_ctx.is_expired:
    print("This context has expired")
```

## Migration Guide

### Database Migration

```bash
# Run the migration
uv run alembic upgrade head
```

### Updating Existing Code

**Before:**
```python
context = Context(
    content="Full content here",
    summary="Short summary",
    keywords=["keyword1", "keyword2"]
)
```

**After:**
```python
context = Context(
    glance="One-line summary",      # NEW: Quick scan
    summary="Structured overview",   # SAME: Overview layer
    content="Full content here",     # SAME: Detail layer
    tags=["tag1", "tag2"]           # NEW: Replaces keywords
)
```

### Backward Compatibility

- `keywords` field is retained for backward compatibility but deprecated
- If `glance` is not provided, it will auto-generate from `summary` or `content`
- Existing contexts without `glance` or `tags` will still work

## Path-Based Queries

### Using with ContextPathSuffix Constants

```python
from structure.plugins.executors.conflict.prompts import (
    ContextPathSuffix,
    build_context_path,
    get_all_context_paths
)

# Build standard workspace paths
workspace_id = "ws_123"
tools_path = build_context_path(workspace_id, ContextPathSuffix.TOOLS)
# Result: "/ws_123/tools"

# Get all standard paths
paths = get_all_context_paths(workspace_id)
# {
#   "tools": "/ws_123/tools",
#   "skills": "/ws_123/skills",
#   "knowledge": "/ws_123/knowledge",
#   ...
# }

# Query contexts using standard paths
stmt = select(Context).where(
    Context.path.like(f"{paths['tools']}/%")
)
```

### Glob-Style Queries (Application Level)

While the database doesn't support glob patterns directly, you can implement them at the application level:

```python
import fnmatch

def glob_contexts(session, pattern: str) -> list[Context]:
    """Query contexts using glob patterns.

    Examples:
        glob_contexts(session, "/ws_123/*/nginx")      # All nginx under any server
        glob_contexts(session, "/ws_123/web-01/**")    # All under web-01
    """
    # Get all contexts (or use path prefix if pattern starts with a literal)
    contexts = session.execute(select(Context)).scalars().all()

    # Filter using fnmatch
    return [
        ctx for ctx in contexts
        if ctx.path and fnmatch.fnmatch(ctx.path, pattern)
    ]
```

## Best Practices

### 1. Always Provide Glance

```python
# ✅ Good
context = Context(
    glance="Web Search Tool — Search using DuckDuckGo",
    summary="Performs web searches...",
    content=full_content
)

# ❌ Bad
context = Context(
    summary="Performs web searches...",
    content=full_content
)
```

### 2. Use Tags Instead of Keywords

```python
# ✅ Good
context = Context(
    tags=["python", "documentation", "api"]
)

# ❌ Deprecated
context = Context(
    keywords=["python", "documentation", "api"]
)
```

### 3. Structure Paths Hierarchically

```python
# ✅ Good
/workspace_123/tools/web_search
/workspace_123/knowledge/python/best_practices
/workspace_123/skills/code_review

# ❌ Bad
/workspace_123/web_search_tool
/workspace_123/python_bp
```

### 4. Use Progressive Disclosure

```python
# ✅ Good - start with glance, drill down as needed
contexts = get_contexts()
for ctx in contexts:
    print(ctx.disclose("glance"))

    if user_wants_more:
        print(ctx.disclose("overview"))

    if user_wants_details:
        print(ctx.disclose("detail"))

# ❌ Bad - always fetch full content
for ctx in contexts:
    print(ctx.content)  # Inefficient for large datasets
```

## Performance Considerations

1. **Indexes**: New indexes on `glance` and `(user_id, path)` improve query performance
2. **Progressive Disclosure**: Use glance queries when you only need summaries
3. **Tag Queries**: JSONB tag queries use GIN indexes for efficiency
4. **Path Prefix Queries**: Use `LIKE 'prefix%'` for efficient prefix matching

## Testing

```bash
# Test model imports
uv run python -c "from aiwen.models.context import Context, WorkspaceContext; print('✓ Models OK')"

# Test schema imports
uv run python -c "from aiwen.schemas.context import ContextCreate; print('✓ Schemas OK')"

# Run migration
uv run alembic upgrade head

# Verify migration
uv run alembic current
```
