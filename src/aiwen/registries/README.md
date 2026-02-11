# Centralized Registry System

This module provides a unified, extensible registry architecture for managing different types of components in the Aiwen Service.

## Overview

The registry system provides:

- **Consistent patterns** across all registry types (tools, agents, models, etc.)
- **Protocol-based interfaces** using PEP 544 structural typing for flexibility
- **Flexible configuration** for caching, validation, and persistence
- **Database synchronization** with automatic CRUD operations
- **Lifecycle hooks** for custom behavior on registration/retrieval
- **Type safety** with runtime protocol validation
- **Backward compatibility** with existing code
- **Centralized management** through RegistryManager

## Architecture

```
registries/
├── __init__.py               # Public API exports
├── core.py                   # BaseRegistry + concrete implementations
├── manager.py                # RegistryManager for coordinating registries
├── base_class/               # Base classes for components
│   ├── base_tool.py         # BaseTool (ABC) - implements ToolProtocol
│   ├── base_executor.py     # Executor Protocol re-export
│   └── __init__.py
└── README.md                # This file

core/interfaces/              # Protocol definitions
├── protocols.py             # All protocol definitions
├── executor.py              # Executor Protocol implementation
├── tool.py                  # Tool interface exports
└── __init__.py
```

### Key Components

#### 1. Protocol Layer (Structural Typing)

Defines interfaces for registrable components using PEP 544 Protocols:

- **RegistrableProtocol**: Base protocol for all registrable components
- **ToolProtocol**: Interface for tool components
- **ExecutorProtocol**: Interface for executor components
- **RegistryProtocol**: Interface for registry implementations

**Benefits:**
- Structural typing (no mandatory inheritance)
- Better type safety with static type checkers
- Easier third-party integration
- Clear interface documentation

See [PROTOCOLS.md](../../../docs/PROTOCOLS.md) for detailed documentation.

#### 2. BaseRegistry (Abstract Base Class)

Provides common functionality for all registries (implements RegistryProtocol):

- **Registration**: Register components with validation
- **Retrieval**: Get components or instances (with optional caching)
- **Filtering**: Query components by custom predicates
- **Database sync**: Optional persistence to database
- **Lifecycle hooks**: Custom callbacks on register/retrieve
- **Statistics**: Introspection and monitoring

#### 3. RegistryManager (Singleton Coordinator)

Central coordinator for all registries:

- **Singleton access**: Get registry instances by type
- **Batch operations**: Sync all registries to database at once
- **Cross-registry queries**: Get statistics from all registries
- **Lifecycle management**: Clear all registries (for testing)

#### 4. Specific Registries

- **ToolRegistry**: Manages tool classes (InnerTools, ExternalTools)
- **ExecutorRegistry**: Manages agent executor templates

## Usage Examples

### 0. Using Protocols for Type Safety

```python
from aiwen.registries import ToolProtocol, ExecutorProtocol, is_tool, is_executor

# Type-safe function accepting any tool-like object
async def execute_tool(tool: ToolProtocol, input_data: dict) -> dict:
    """Works with any object implementing ToolProtocol."""
    validated = await tool.validate_input(input_data)
    result = await tool.execute(validated)
    return tool.format_output(result)

# Runtime validation
if is_tool(my_component):
    await execute_tool(my_component, {"param": "value"})

# Check protocol requirements
from aiwen.registries import PROTOCOL_REGISTRY
print(PROTOCOL_REGISTRY["ToolProtocol"])
# Shows required attributes and methods
```

**Benefits:**
- Works with any tool-like object, not just BaseTool subclasses
- Static type checkers verify protocol compliance
- Clear interface contracts
- Easier testing with mocks

See [PROTOCOLS.md](../../../docs/PROTOCOLS.md) for comprehensive protocol documentation.

### 1. Register a Tool

```python
from aiwen.registries import register_tool
from aiwen.services.tools.base_tool import BaseTool, ToolMetadata

@register_tool
class MyTool(BaseTool):
    METADATA = ToolMetadata(
        name="my_tool",
        display_name="My Tool",
        description="Does something useful",
        category="utility",
        execution_mode=ToolExecutionMode.SERVER_RUN,
    )

    class InputSchema(ToolInputSchema):
        text: str = Field(description="Input text")

    async def execute(self, text: str) -> dict:
        return {"result": text.upper()}
```

### 2. Register an Executor

```python
from aiwen.registries import register_executor
from aiwen.registries.base_class.base_executor import Executor


@register_executor
class MyExecutor(Executor):
    TEMPLATE = {
        "template_code": "MY001",
        "template_name": "My Agent",
        "enabled": True,
        "version": 1,
        "config": {}
    }

    async def execute(self, run_id: str) -> None:
        # Agent execution logic
        pass
```

### 3. Access Registries

```python
from aiwen.registries import get_registry, ToolRegistry, ExecutorRegistry

# Get tool registry
tool_registry = get_registry(ToolRegistry)

# List all tools
all_tools = tool_registry.list_tools()

# Get tool instance (singleton)
tool = tool_registry.get_instance("my_tool")

# Get executor registry
executor_registry = get_registry(ExecutorRegistry)

# Get executor class
executor_cls = executor_registry.get("MY001")
```

### 4. Database Synchronization

```python
from aiwen.registries import sync_all_registries
from aiwen.extensions.database import get_session

# Sync all registries to database at startup
async with get_session("aiwen") as db:
    await sync_all_registries(db)
```

### 5. Filter and Query

```python
# Filter tools by execution mode
server_tools = tool_registry.list_tools(
    execution_mode=ToolExecutionMode.SERVER_RUN,
    enabled_only=True
)

# Query tools by tag
ml_tools = tool_registry.get_tools_by_tag("machine-learning")

# Custom filter
def is_beta_tool(name: str, tool_class: type[BaseTool]) -> bool:
    return "beta" in tool_class.METADATA.tags

beta_tools = tool_registry.filter(is_beta_tool)
```

### 6. Statistics and Monitoring

```python
from aiwen.registries import RegistryManager

manager = RegistryManager.get_instance()

# Get statistics from all registries
all_stats = manager.get_statistics()

# Get statistics from specific registry
tool_stats = tool_registry.get_statistics()
# Returns:
# {
#     "total_tools": 26,
#     "enabled_tools": 24,
#     "disabled_tools": 2,
#     "by_execution_mode": {"SERVER_RUN": 18, "HTTP": 5, ...},
#     "by_category": {"database": 8, "browser": 6, ...}
# }
```

## Configuration

Each registry can be configured via `RegistryConfig`:

```python
from aiwen.registries.base import RegistryConfig

config = RegistryConfig(
    # Persistence
    enable_db_sync=True,       # Sync to database on startup
    enable_lazy_load=False,    # Load components on-demand

    # Caching
    enable_instance_cache=True,  # Cache instances (singleton pattern)
    cache_ttl=None,             # Cache TTL in seconds (None = forever)

    # Validation
    validate_on_register=True,  # Validate component on registration
    allow_override=False,       # Allow re-registration with same key

    # Discovery
    auto_discover=True,         # Auto-import modules to trigger decorators
    discovery_paths=[],         # Paths to search for components

    # Logging
    log_registration=True,      # Log component registration
)

# Create registry with custom config
registry = ToolRegistry(config=config)
```

## Extending the System

### Create a New Registry

1. **Define your registry class**:

```python
from aiwen.registries.base import BaseRegistry, RegistryConfig
from typing import Optional

class ModelRegistry(BaseRegistry[str, "ModelConfig"]):
    """Registry for LLM model configurations."""

    def __init__(self, config: Optional[RegistryConfig] = None):
        if config is None:
            config = RegistryConfig(
                enable_db_sync=True,
                enable_instance_cache=False,
            )
        super().__init__(config)

    def _validate_component(self, model_config: "ModelConfig") -> None:
        """Validate model configuration."""
        if not model_config.model_name:
            raise ValueError("model_name is required")

    def _extract_key(self, model_config: "ModelConfig") -> str:
        """Extract model name as key."""
        return model_config.model_name

    def _create_instance(self, key: str, model_config: "ModelConfig", **kwargs):
        """Create model instance (if needed)."""
        return model_config  # Or create actual model client

    async def _sync_to_database(self, db: AsyncSession) -> None:
        """Sync models to database (optional)."""
        # Implement database sync logic
        pass
```

2. **Create a decorator**:

```python
def register_model(model_config: "ModelConfig") -> "ModelConfig":
    """Decorator to register model configuration."""
    from aiwen.registries.manager import get_registry
    registry = get_registry(ModelRegistry)
    return registry.register(model_config)
```

3. **Register with RegistryManager**:

```python
from aiwen.registries import register_registry

# Register during app startup
register_registry(ModelRegistry)
```

4. **Export from __init__.py**:

```python
# src/aiwen/registries/__init__.py
from aiwen.registries.model_registry import ModelRegistry, register_model

__all__ = [
    # ... existing exports
    "ModelRegistry",
    "register_model",
]
```

## Migration Guide

### Migrating from Old ToolRegistry

**Old code** (scattered in `services/tools/` - deprecated):
```python
# DEPRECATED - Do not use
from aiwen.services.tools.tool_registry import ToolRegistry

# Register tool
ToolRegistry.register(MyTool)

# Get tool
tool_class = ToolRegistry.get_tool_class("my_tool")
tool_instance = ToolRegistry.get_tool_instance("my_tool")
```

**New code** (centralized in `registries/`):
```python
from aiwen.registries import get_registry, ToolRegistry

# Register tool (same decorator)
@register_tool
class MyTool(BaseTool):
    ...

# Get tool (backward compatible methods still work)
tool_class = ToolRegistry.get_tool_class("my_tool")
tool_instance = ToolRegistry.get_tool_instance("my_tool")

# Or use new instance-based API
registry = get_registry(ToolRegistry)
tool_class = registry.get("my_tool")
tool_instance = registry.get_instance("my_tool")
```

### Migrating from Old ExecutorRegistry

**Old code** (deprecated):
```python
# DEPRECATED - Do not use
from aiwen.services.executor.executor_registry import ExecutorRegistry

# Get executor
executor_cls = ExecutorRegistry.get("MY001")

# List executors
templates = ExecutorRegistry.list()
```

**New code**:
```python
from aiwen.registries import get_registry, ExecutorRegistry

# Same API (backward compatible)
executor_cls = ExecutorRegistry.get("MY001")
templates = ExecutorRegistry.list()

# Or use new instance-based API
registry = get_registry(ExecutorRegistry)
executor_cls = registry.get("MY001")
templates = registry.list_templates()
```

### Updating Bootstrap Code

**Old code** (`core/bootstrap.py` - deprecated):
```python
# DEPRECATED - Do not use
from aiwen.services.tools.inner_tool_sync import sync_inner_tools_to_db
from aiwen.services.executor.executor_registry import init_executor_registry

await sync_inner_tools_to_db(db)
await init_executor_registry()
```

**New code**:
```python
from aiwen.registries import sync_all_registries
from aiwen.extensions.database import get_session

# Sync all registries at once
async with get_session("aiwen") as db:
    await sync_all_registries(db)
```

## Testing

### Clear Registries in Tests

```python
from aiwen.registries import RegistryManager

def test_my_tool():
    # Clear all registries before test
    manager = RegistryManager.get_instance()
    manager.clear_all()

    # Register test tool
    @register_tool
    class TestTool(BaseTool):
        ...

    # Run test
    assert ToolRegistry.get_tool_class("test_tool") is not None
```

### Mock Registry Behavior

```python
from unittest.mock import Mock
from aiwen.registries import ToolRegistry

def test_with_mock_registry(monkeypatch):
    mock_registry = Mock(spec=ToolRegistry)
    mock_registry.get_instance.return_value = Mock()

    # Replace registry in tests
    monkeypatch.setattr("aiwen.registries.tool_registry.ToolRegistry", mock_registry)
```

## Best Practices

1. **Use decorators for registration**: Always prefer `@register_tool` and `@register_executor` over explicit registration

2. **Access via RegistryManager**: Use `get_registry(RegistryClass)` for consistent access

3. **Sync at startup**: Call `sync_all_registries(db)` during application bootstrap

4. **Validate metadata**: Ensure METADATA and TEMPLATE attributes are complete and valid

5. **Don't cache instances unnecessarily**: Set `enable_instance_cache=False` if components are stateful or run-specific

6. **Use lifecycle hooks sparingly**: Only add hooks when necessary for cross-cutting concerns

7. **Test with clean slate**: Always clear registries in tests to avoid pollution

## Troubleshooting

### "Component not found" errors

**Cause**: Component was not registered (decorator not executed)

**Solution**: Ensure module is imported before accessing registry

```python
# Force import to trigger decorators
import aiwen.services.tools.inner_tool.server_tools
import aiwen.services.executor.executor_template.default.concrete
```

### "Already registered" errors

**Cause**: Attempting to register same key twice

**Solution**: Set `allow_override=True` in config, or check if registered first

```python
if not registry.is_registered("my_tool"):
    registry.register(MyTool)
```

### Database sync failures

**Cause**: Database connection issues or migration not applied

**Solution**: Check database connection and run migrations

```bash
make db-upgrade
```

## Future Enhancements

- [ ] Model Registry for LLM configurations
- [ ] Skill Registry for agent skills
- [ ] Plugin Registry for dynamic extensions
- [ ] Metrics and monitoring hooks
- [ ] Registry versioning and rollback
- [ ] Distributed registry for multi-node deployments
