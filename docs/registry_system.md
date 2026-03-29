# Registry System Architecture

## Overview

The Aiwen Service uses a centralized registry system to manage different types of components (tools, agents, models, etc.) with consistent patterns for registration, discovery, and lifecycle management.

## Folder Structure

```
src/aiwen/
├── registries/                    # 🆕 Centralized registry system
│   ├── __init__.py               # Public API exports
│   ├── base.py                   # BaseRegistry abstract class
│   ├── manager.py                # RegistryManager coordinator
│   ├── tool_registry.py          # ToolRegistry implementation
│   ├── executor_registry.py      # ExecutorRegistry implementation
│   └── README.md                 # Documentation
│
├── services/
│   ├── tools/
│   │   ├── tool_registry.py      # ⚠️ Old location (backward compat)
│   │   ├── base_tool.py          # BaseTool, InnerTool, ExternalTool
│   │   ├── dynamic_tool_loader.py
│   │   └── inner_tool_sync.py
│   │
│   └── executor/
│       ├── executor_registry.py   # ⚠️ Old location (backward compat)
│       ├── base.py                # Executor base class
│       └── executor_template/     # Executor implementations
│
└── core/
    └── bootstrap.py              # App initialization
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Application Layer                         │
│  (API Routes, Workers, Services use registries via decorators)  │
└────────────┬───────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      RegistryManager                             │
│                      (Singleton)                                 │
│                                                                   │
│  • Coordinates all registries                                    │
│  • Provides unified access: get_registry(RegistryClass)         │
│  • Batch operations: sync_all_to_database()                     │
│  • Cross-registry queries: get_statistics()                     │
└────────────┬──────────────────────────────┬─────────────────────┘
             │                              │
             ▼                              ▼
┌────────────────────────────┐  ┌────────────────────────────┐
│     ToolRegistry           │  │   ExecutorRegistry         │
│                            │  │                            │
│  • Manages tools           │  │  • Manages executors       │
│  • InnerTool & ExternalTool│  │  • Agent templates         │
│  • OpenAI/LangChain schemas│  │  • Dual persistence        │
│  • Filter by mode/category │  │  • Soft delete support     │
└────────────┬───────────────┘  └────────────┬───────────────┘
             │                               │
             │  Extends                      │  Extends
             ▼                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    BaseRegistry<K, T>                            │
│                    (Abstract Base Class)                         │
│                                                                   │
│  Core Methods:                                                   │
│  • register(component)         - Register component             │
│  • get(key)                    - Get component by key           │
│  • get_instance(key)           - Get/create instance (cached)   │
│  • filter(predicate)           - Query by custom logic          │
│  • sync_to_database(db)        - Persist to database            │
│  • get_statistics()            - Introspection                  │
│                                                                   │
│  Abstract Methods (must implement):                             │
│  • _validate_component()       - Validate before registration   │
│  • _extract_key()              - Get unique key from component  │
│  • _create_instance()          - Instantiate component          │
│  • _sync_to_database()         - Database sync logic (optional) │
│                                                                   │
│  Configuration (RegistryConfig):                                │
│  • enable_db_sync              - Database persistence           │
│  • enable_instance_cache       - Singleton pattern              │
│  • validate_on_register        - Validate on registration       │
│  • allow_override              - Allow duplicate keys           │
└─────────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Database Layer                               │
│                                                                   │
│  • Tool table (tool_type: inner/external)                       │
│  • AgentTemplate table                                          │
│  • Sync on startup via _sync_to_database()                      │
└─────────────────────────────────────────────────────────────────┘
```

## Registration Flow

### 1. Component Registration (Import Time)

```
┌─────────────────────────────────────────────────────────────────┐
│  1. Module Import                                                │
│     import aiwen.services.tools.inner_tool.server_tools         │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│  2. Decorator Execution                                          │
│     @register_tool                                               │
│     class MyTool(BaseTool):                                     │
│         METADATA = ToolMetadata(...)                            │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│  3. Registry Registration                                        │
│     registry = ToolRegistry._get_singleton_instance()           │
│     registry.register(MyTool)                                   │
│       ├─ Validate: _validate_component(MyTool)                  │
│       ├─ Extract key: _extract_key(MyTool) → "my_tool"         │
│       └─ Store: _registry["my_tool"] = MyTool                   │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│  4. In-Memory Registry                                           │
│     ToolRegistry._registry = {                                  │
│         "my_tool": MyTool,                                      │
│         "another_tool": AnotherTool,                            │
│         ...                                                      │
│     }                                                            │
└─────────────────────────────────────────────────────────────────┘
```

### 2. Database Synchronization (Startup)

```
┌─────────────────────────────────────────────────────────────────┐
│  1. App Bootstrap                                                │
│     ApplicationBootstrap.initialize()                           │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│  2. Import All Components                                        │
│     _import_all_executor()  # Auto-discover executors           │
│     # Tools imported via module imports                          │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│  3. Sync All Registries                                          │
│     await sync_all_registries(db)                               │
│       ├─ ToolRegistry.sync_to_database(db)                      │
│       │   ├─ For each InnerTool in _registry:                   │
│       │   │   ├─ Check if exists in Tool table                  │
│       │   │   ├─ Create/update Tool record                      │
│       │   │   └─ Set tool_type='inner'                          │
│       │   └─ Commit transaction                                 │
│       │                                                          │
│       └─ ExecutorRegistry.sync_to_database(db)                  │
│           ├─ For each Executor in _registry:                    │
│           │   ├─ Check if exists in AgentTemplate table         │
│           │   ├─ Create/update AgentTemplate record             │
│           │   └─ Re-enable if previously deleted                │
│           ├─ Mark unregistered templates as deleted             │
│           └─ Commit transaction                                 │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│  4. Database Synchronized                                        │
│     • Tool table: 26 InnerTools synced                          │
│     • AgentTemplate table: All executors synced                 │
│     • Orphaned templates marked as deleted                      │
└─────────────────────────────────────────────────────────────────┘
```

### 3. Component Retrieval (Runtime)

```
┌─────────────────────────────────────────────────────────────────┐
│  1. Request Tool Instance                                        │
│     tool = ToolRegistry.get_tool_instance("my_tool")            │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│  2. Check Cache (if enabled)                                     │
│     if "my_tool" in _instances:                                 │
│         return _instances["my_tool"]  ← Cache hit!              │
└────────────┬────────────────────────────────────────────────────┘
             │ Cache miss
             ▼
┌─────────────────────────────────────────────────────────────────┐
│  3. Create Instance                                              │
│     tool_class = _registry["my_tool"]                           │
│     instance = _create_instance("my_tool", tool_class)          │
│       └─ instance = MyTool()  # Instantiate                     │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│  4. Cache Instance (if enabled)                                  │
│     _instances["my_tool"] = instance                            │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────┐
│  5. Return Instance                                              │
│     return instance                                              │
└─────────────────────────────────────────────────────────────────┘
```

## Migration Strategy

### Phase 1: Create New Registry System (✅ Complete)

- [x] Create `src/aiwen/registries/` folder
- [x] Implement `BaseRegistry` abstract class
- [x] Implement `RegistryManager` coordinator
- [x] Create `ToolRegistry` extending `BaseRegistry`
- [x] Create `ExecutorRegistry` extending `BaseRegistry`
- [x] Add comprehensive documentation

### Phase 2: Update Imports (Recommended)

Update code to use new centralized location:

```python
# Old imports
from structure.services.tools.tool_registry import ToolRegistry
from structure.services.executor.executor_registry import ExecutorRegistry

# New imports
from structure.registries import ToolRegistry, ExecutorRegistry, get_registry
```

### Phase 3: Update Bootstrap Code (Recommended)

Simplify app initialization:

```python
# Old code (core/bootstrap.py)
from structure.services.tools.inner_tool_sync import sync_inner_tools_to_db
from structure.services.executor.executor_registry import init_executor_registry


async def _initialize_agent_registry(self):
    await init_executor_registry()


async def _sync_inner_tools(self):
    async with get_session("structure") as session:
        await sync_inner_tools_to_db(session)


# New code (simplified)
from structure.registries import sync_all_registries


async def _initialize_registries(self):
    async with get_session("structure") as session:
        await sync_all_registries(session)
```

### Phase 4: Add Backward Compatibility Wrappers (Optional)

Keep old imports working during transition:

```python
# src/structure/services/tools/tool_registry.py
"""
Backward compatibility wrapper for ToolRegistry.
New code should import from structure.registries instead.
"""
from structure.registries import ToolRegistry, register_tool

__all__ = ["ToolRegistry", "register_tool"]
```

### Phase 5: Deprecate Old Locations (Future)

Add deprecation warnings:

```python
import warnings

warnings.warn(
    "Importing from structure.services.tools.tool_registry is deprecated. "
    "Use 'from structure.registries import ToolRegistry' instead.",
    DeprecationWarning,
    stacklevel=2
)
```

## Key Benefits

### 1. Consistency

All registries follow the same patterns:
- Registration via decorators
- Validation on registration
- Filtering and querying
- Database synchronization
- Statistics and introspection

### 2. Extensibility

Easy to add new registry types:
```python
class ModelRegistry(BaseRegistry[str, "ModelConfig"]):
    """Registry for LLM model configurations."""
    # Implement abstract methods
    ...
```

### 3. Testability

Centralized management makes testing easier:
```python
def test_my_feature():
    manager = RegistryManager.get_instance()
    manager.clear_all()  # Clean slate for each test
    # ... test code
```

### 4. Maintainability

Single source of truth for registry logic:
- Bug fixes in `BaseRegistry` benefit all registries
- Consistent behavior across all component types
- Clear extension points for customization

### 5. Monitoring

Unified statistics and introspection:
```python
manager = RegistryManager.get_instance()
stats = manager.get_statistics()
# {
#   "ToolRegistry": {"total_components": 26, ...},
#   "ExecutorRegistry": {"total_components": 1, ...}
# }
```

## Performance Considerations

### Memory Usage

- **Instance Caching**: Tools use singleton pattern (1 instance per tool)
- **Class Storage**: Only store classes, not instances (executors)
- **Lazy Loading**: Components created on first access

### Startup Time

- **Auto-Discovery**: Executors auto-discovered via file system scan
- **Database Sync**: Batch operations with single transaction
- **Import Time**: Decorators execute at import (one-time cost)

### Runtime Performance

- **O(1) Lookups**: Dictionary-based storage for fast retrieval
- **Filtering**: In-memory filtering (no database queries)
- **Caching**: Instance cache eliminates repeated instantiation

## Troubleshooting

### Issue: Component Not Found

**Symptoms**: `KeyError: "Component 'my_tool' not found"`

**Cause**: Module not imported (decorator not executed)

**Solution**: Import module before accessing registry

```python
import structure.services.tools.inner_tool.server_tools  # Trigger decorators

tool = ToolRegistry.get_tool_instance("my_tool")
```

### Issue: Already Registered Error

**Symptoms**: `ValueError: Component 'my_tool' already registered`

**Cause**: Duplicate registration (usually in tests)

**Solution**: Clear registry or check before registering
```python
registry.clear()  # In test setup
# or
if not registry.is_registered("my_tool"):
    registry.register(MyTool)
```

### Issue: Database Sync Failed

**Symptoms**: Errors during `sync_to_database()`

**Cause**: Database migration not applied or connection issue

**Solution**: Run migrations
```bash
make db-upgrade
make db-current  # Verify migration applied
```

## Future Enhancements

- [ ] **ModelRegistry**: LLM model configurations
- [ ] **SkillRegistry**: Agent skills and capabilities
- [ ] **PluginRegistry**: Dynamic plugin system
- [ ] **Metrics**: Prometheus metrics for registration/retrieval
- [ ] **Versioning**: Registry versioning and rollback
- [ ] **Distributed**: Multi-node registry synchronization
- [ ] **Hot Reload**: Dynamic component reloading without restart
