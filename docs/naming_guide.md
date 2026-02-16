# Naming Guide and Best Practices

This document clarifies naming conventions and helps avoid confusion when working with similarly named classes.

## Context-Related Classes

The "Context" concept appears in multiple layers with different purposes. Here's how to distinguish them:

### 1. **Database Layer (Persistence)**

```python
from aiwen.models.context.context import Context
```

**Purpose**: PostgreSQL table ORM model for storing agent contexts with embeddings.

**When to use**:
- Direct database queries via SQLAlchemy
- When you need persistent storage with vector search

**Example**:
```python
# Query contexts from database
stmt = select(Context).where(Context.user_id == user_id)
contexts = await session.execute(stmt)
```

---

### 2. **Framework Layer (In-Memory)**

```python
from aiwen.frameworks.context import ContextStore, ContextEntry
```

**Purpose**: In-memory hierarchical storage with path-based addressing.

**When to use**:
- Fast hierarchical queries (tree, glob, children)
- Temporary context during execution
- When you need Trie-indexed path lookups

**Key Classes**:
- `ContextStore` - Path-addressable in-memory storage
- `ContextEntry` - Single node with glance/overview/detail
- `DetailLevel` - Progressive disclosure levels

**Example**:
```python
# In-memory hierarchical storage
store = ContextStore("My Context")
store.set("cluster/web-01", glance="Web server", overview={"cpu": "23%"})
results = store.glob("cluster/**")  # Fast Trie-based search
```

---

### 3. **API Layer (Schemas)**

```python
from aiwen.schemas.context.context_schema import (
    ContextCreate,
    ContextUpdate,
    ContextResponse,
)
```

**Purpose**: Pydantic models for API request/response validation.

**When to use**:
- API endpoint input validation
- Response serialization
- OpenAPI documentation generation

**Example**:
```python
@router.post("/contexts", response_model=ContextResponse)
async def create_context(data: ContextCreate):
    ...
```

---

### 4. **Service Layer (Business Logic)**

```python
from aiwen.services.context.context_crud import ContextCRUD
from aiwen.services.workspace_context.workspace_context_service import WorkspaceContextService
```

**Purpose**: Business logic combining database + framework layers.

**Key Classes**:
- `ContextCRUD` - Database CRUD operations for Context model
- `WorkspaceContextService` - Hybrid persistence + in-memory cache

**When to use**:
- Business operations (create, update, search)
- When you need both persistence AND fast queries

**Example**:
```python
# Workspace service combines DB + ContextStore
service = WorkspaceContextService(session, workspace_id)
await service.load()  # Load from DB into ContextStore
results = await service.glob("tools/**")  # Fast in-memory query
await service.set("tools/new", glance="...")  # Auto-syncs to DB
```

---

## Executor-Related Classes

### 1. **Interface (Abstract)**

```python
from aiwen.core.interfaces.executor import Executor
```

**Purpose**: Abstract base class defining the executor interface.

**When to use**: Implementing new executor types (inherit from this).

---

### 2. **Database Model (Persistence)**

```python
from aiwen.models.executor import ExecutorTemplate
```

**Purpose**: Database table for storing executor configurations.

**When to use**: Querying or persisting executor templates.

**Note**: Previously named `Executor` - renamed to avoid confusion with the interface.

---

### 3. **Service Layer**

```python
from aiwen.services.executor.executor_crud import ExecutorCRUD
from aiwen.services.executor.runtime import ExecutorInstanceManager
```

**Key Classes**:
- `ExecutorCRUD` - Database operations for ExecutorTemplate
- `ExecutorInstanceManager` - Manages running executor instances (formerly `ExecutorRuntime`)

---

## Tool-Related Classes

### 1. **Interface**

```python
from aiwen.core.interfaces.tool import BaseTool
```

**Purpose**: Abstract base class for all tools.

---

### 2. **Database Model**

```python
from aiwen.models.context.tools import Tool
```

**Purpose**: Database table for tool definitions.

**Note**: `UserTool` is a backward-compatibility alias for `Tool`.

---

### 3. **Schemas**

```python
from aiwen.schemas.context.tools.user_tool import (
    UserToolCreate,
    UserToolUpdate,
    UserToolResponse,
)
```

**Purpose**: API schemas for user-defined (custom) tools.

---

## General Naming Patterns

### Suffixes

| Suffix | Layer | Example |
|--------|-------|---------|
| (none) | ORM Model | `Context`, `Tool`, `Run` |
| `Template` | Configuration Model | `ExecutorTemplate` |
| `Create` | API Request | `ContextCreate`, `ToolCreate` |
| `Update` | API Patch Request | `ContextUpdate`, `ToolUpdate` |
| `Response` | API Response | `ContextResponse`, `ToolResponse` |
| `ListResponse` | List API Response | `ContextListResponse` |
| `CRUD` | Database Service | `ContextCRUD`, `ToolCRUD` |
| `Service` | Business Service | `WorkspaceContextService` |
| `Manager` | Instance Manager | `ExecutorInstanceManager` |

### Prefixes

| Prefix | Meaning | Example |
|--------|---------|---------|
| `Base` | Abstract base class | `BaseTool`, `BaseEventSchema` |
| `User` | User-defined/custom | `UserTool`, `UserToolCreate` |
| `Workspace` | Workspace-scoped | `WorkspaceContext`, `WorkspaceResponse` |

---

## Quick Decision Guide

**"I need to store context in the database"**
→ Use `Context` (ORM model) + `ContextCRUD` (service)

**"I need fast hierarchical queries during execution"**
→ Use `ContextStore` (framework)

**"I need both persistence AND fast queries"**
→ Use `WorkspaceContextService` (combines both)

**"I'm building an API endpoint"**
→ Use `Context*` schemas (`ContextCreate`, `ContextResponse`, etc.)

**"I'm implementing a new executor"**
→ Inherit from `Executor` (interface)

**"I'm storing executor configuration in DB"**
→ Use `ExecutorTemplate` (ORM model) + `ExecutorCRUD` (service)

---

## Migration Notes

### Recent Renamings

| Old Name | New Name | Reason |
|----------|----------|--------|
| `Executor` (model) | `ExecutorTemplate` | Conflict with interface |
| `ExecutorRuntime` | `ExecutorInstanceManager` | Clearer semantics |
| `executor_template_crud.py` | `executor_crud.py` | Consistency |

### Backward Compatibility

```python
# Old imports still work (with deprecation warnings)
from aiwen.models.executor import Executor  # Works, but use ExecutorTemplate
```

---

## Contributing

When adding new classes:

1. **Choose clear, specific names** that indicate the layer (model, schema, service)
2. **Add docstrings** that clarify the class purpose and distinguish from similar classes
3. **Use consistent suffixes** (Create, Update, Response, CRUD, Service)
4. **Document in this guide** if the name could be confused with existing classes

---

*Last updated: 2026-02-16*
