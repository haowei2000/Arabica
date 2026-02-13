# Protocol Layer Documentation

## Overview

The protocol layer provides structural type definitions (PEP 544 Protocols) for all components that can be registered in the registry system. Using protocols instead of abstract base classes provides:

- **Structural typing**: Duck typing with type safety
- **No mandatory inheritance**: Components can implement the protocol without explicit inheritance
- **Flexibility**: Easier to integrate third-party code
- **Clear contracts**: Explicit interface documentation

## Architecture

```
core/interfaces/
├── protocols.py          # Protocol definitions
├── executor.py          # Executor implementation (already a Protocol)
├── tool.py              # Tool interface exports
└── __init__.py          # Public API

registries/base_class/
├── base_tool.py         # BaseTool (ABC) - implements ToolProtocol
├── base_executor.py     # Re-exports Executor Protocol
└── __init__.py
```

## Available Protocols

### 1. RegistrableProtocol

Base protocol for all components that can be registered in a registry.

```python
from aiwen.interfaces import RegistrableProtocol


class MyComponent(RegistrableProtocol):
    def __repr__(self) -> str:
        return f"<MyComponent>"
```

**Requirements:**
- `__repr__()`: String representation

### 2. ToolProtocol

Protocol for tool components registered in `ToolRegistry`.

```python
from aiwen.interfaces import ToolProtocol
from aiwen.registries.base_class import (
    ToolMetadata,
    ToolInputSchema,
    ToolOutputSchema,
)
from typing import ClassVar


class MyTool(ToolProtocol):
    # Required class attributes
    METADATA: ClassVar[ToolMetadata] = ToolMetadata(
        name="my_tool",
        display_name="My Tool",
        description="Does something",
    )

    InputSchema: ClassVar[type[ToolInputSchema]] = ToolInputSchema
    OutputSchema: ClassVar[type[ToolOutputSchema]] = ToolOutputSchema

    # Required methods
    async def execute(self, input_data):
        return self.OutputSchema(
            success=True,
            message="Done",
            data={"result": "ok"}
        )

    async def validate_input(self, raw_input):
        return self.InputSchema(**raw_input)

    def format_output(self, output):
        return output.model_dump()

    @classmethod
    def get_json_schema(cls):
        return {...}

    @classmethod
    def get_metadata(cls):
        return cls.METADATA

    # ... other required methods
```

**Requirements:**
- **Attributes**: `METADATA`, `InputSchema`, `OutputSchema`
- **Methods**: `execute()`, `validate_input()`, `format_output()`, `get_json_schema()`, etc.
- **Hooks**: `before_execute()`, `after_execute()`, `on_error()` (optional)

**Note**: In practice, you should inherit from `BaseTool` which already implements this protocol:

```python
from aiwen.registries.base_class import BaseTool

class MyTool(BaseTool):
    METADATA = ToolMetadata(...)

    async def execute(self, input_data):
        # Your implementation
        pass
```

### 3. ExecutorProtocol

Protocol for executor (agent) components registered in `ExecutorRegistry`.

```python
from aiwen.interfaces import ExecutorProtocol
from typing import ClassVar


class MyExecutor(ExecutorProtocol):
    # Required class attribute
    TEMPLATE: ClassVar[dict] = {
        "template_code": "MY_EXECUTOR",
        "template_name": "My Executor",
        "enabled": True,
        "version": 1,
        "config": {}
    }

    # Required instance attribute
    config: dict

    def __init__(self, config: dict):
        self.config = config

    # Required methods
    async def setup(self):
        # Initialize resources
        pass

    async def run(self, user_message):
        # Execute and return result
        return {"answer": "Response"}

    async def stream(self, user_message, tool_registry):
        # Optional: Stream events
        result = await self.run(user_message)
        yield self._emit_message(result["answer"])

    # Event emission helpers
    def _emit_message(self, content: str):
        # ...
        pass
```

**Requirements:**
- **Attributes**: `TEMPLATE` (class-level), `config` (instance-level)
- **Methods**: `setup()`, `run()`, optionally `stream()`
- **Event helpers**: `_emit_token()`, `_emit_message()`, `_emit_tool_call()`, etc.

**Note**: The actual `Executor` in `core/interfaces/executor.py` is already defined as a Protocol, so you should use it directly:

```python
from aiwen.interfaces import Executor


class MyExecutor:
    TEMPLATE = {...}

    async def setup(self):
        pass

    async def run(self, user_message):
        return {"answer": "..."}
```

### 4. RegistryProtocol

Protocol for registry implementations.

```python
from aiwen.interfaces import RegistryProtocol


class MyRegistry(RegistryProtocol[str, MyComponentType]):
    def __init__(self):
        self._registry = {}
        self._instances = {}
        self._metadata = {}
        self.config = RegistryConfig()

    def register(self, component, key=None, metadata=None):
        # Registration logic
        pass

    def get(self, key):
        return self._registry.get(key)

    def _validate_component(self, component):
        # Validation logic
        pass

    def _extract_key(self, component):
        # Key extraction logic
        return component.name

    def _create_instance(self, key, component, **kwargs):
        # Instance creation logic
        return component()

    async def _sync_to_database(self, db):
        # Database sync logic
        pass
```

**Requirements:**
- **Attributes**: `_registry`, `_instances`, `_metadata`, `config`
- **Methods**: `register()`, `get()`, `get_instance()`, lifecycle methods
- **Internal methods**: `_validate_component()`, `_extract_key()`, `_create_instance()`, `_sync_to_database()`

**Note**: Inherit from `BaseRegistry` which already implements this protocol:

```python
from aiwen.registries.core import BaseRegistry

class MyRegistry(BaseRegistry[str, MyType]):
    def _validate_component(self, component):
        # Your validation
        pass

    # Implement other abstract methods
```

## Type Checking Helpers

Use runtime type checking to verify protocol compliance:

```python
from aiwen.interfaces import is_tool, is_executor, is_registry

# Check if an object implements a protocol
if is_tool(my_object):
    print("It's a valid tool!")

if is_executor(my_executor):
    print("It's a valid executor!")

if is_registry(my_registry):
    print("It's a valid registry!")
```

## Protocol Metadata

Introspect protocol requirements programmatically:

```python
from aiwen.interfaces import PROTOCOL_REGISTRY

# Get protocol information
tool_info = PROTOCOL_REGISTRY["ToolProtocol"]
print(tool_info["description"])
print(tool_info["required_attributes"])  # ["METADATA", "InputSchema", ...]
print(tool_info["required_methods"])  # ["execute", "validate_input", ...]
```

## Benefits of Protocols

### 1. Structural Typing

No need to inherit from a base class. Just implement the interface:

```python
# Without protocols (nominal typing)
class MyTool(BaseTool):  # Must inherit
    pass

# With protocols (structural typing)
class MyTool:  # No inheritance needed
    METADATA = ...
    async def execute(self, input_data):
        pass
    # Implements all required methods
```

### 2. Third-Party Integration

Easily wrap third-party code:

```python
# Third-party tool
from some_library import ThirdPartyTool

# Adapt it to our protocol
class AdaptedTool:
    METADATA = ToolMetadata(...)

    def __init__(self):
        self.inner = ThirdPartyTool()

    async def execute(self, input_data):
        result = self.inner.run(input_data)
        return ToolOutputSchema(success=True, data=result)

    # ... implement other protocol methods
```

### 3. Type Safety

Static type checkers (mypy, pyright) can verify protocol compliance:

```python
def process_tool(tool: ToolProtocol) -> None:
    # Type checker ensures tool has required methods
    metadata = tool.get_metadata()
    result = await tool.execute(input_data)
```

### 4. Clear Documentation

Protocols serve as explicit interface documentation:

```python
# Anyone can see exactly what a tool needs to implement
from aiwen.interfaces import ToolProtocol

reveal_type(ToolProtocol)  # Shows all required methods and attributes
```

## Best Practices

### 1. Prefer Inheritance for Concrete Components

While protocols enable structural typing, for actual components you should still inherit from base classes:

```python
# ✅ Good: Inherit from BaseTool
from aiwen.registries.base_class import BaseTool

class MyTool(BaseTool):
    METADATA = ...
    async def execute(self, input_data):
        pass

# ❌ Avoid: Implementing protocol from scratch
class MyTool:  # Missing helper methods, validation, etc.
    METADATA = ...
    async def execute(self, input_data):
        pass
```

### 2. Use Protocols for Type Annotations

Use protocols in function signatures for maximum flexibility:

```python
from aiwen.interfaces import ToolProtocol


# ✅ Good: Accept any tool-like object
async def execute_tool(tool: ToolProtocol, input: dict) -> dict:
    return await tool.execute(input)


# ❌ Too restrictive: Only accepts BaseTool instances
async def execute_tool(tool: BaseTool, input: dict) -> dict:
    return await tool.execute(input)
```

### 3. Leverage Protocol Validation

Use `isinstance()` checks with `@runtime_checkable` protocols:

```python
from aiwen.interfaces import ToolProtocol

if isinstance(obj, ToolProtocol):
    # Safe to call tool methods
    metadata = obj.get_metadata()
```

### 4. Document Protocol Requirements

When creating new protocols, document requirements clearly:

```python
@runtime_checkable
class MyProtocol(Protocol):
    """
    Protocol for XYZ components.

    Requirements:
      - Attribute `config`: Configuration dict
      - Method `process()`: Process input and return result
      - Method `validate()`: Validate configuration

    Example:
        class MyComponent(MyProtocol):
            config = {"key": "value"}

            def process(self, input):
                return input * 2

            def validate(self):
                if not self.config:
                    raise ValueError("Config required")
    """
    config: dict

    def process(self, input: Any) -> Any: ...
    def validate(self) -> None: ...
```

## Migration Guide

### Existing Code

No changes needed! Existing code that inherits from base classes automatically satisfies the protocols:

```python
# This code continues to work exactly as before
from aiwen.registries.base_class import BaseTool

class MyTool(BaseTool):
    METADATA = ...
    async def execute(self, input_data):
        pass
```

### Type Annotations

You can gradually update type annotations to use protocols:

```python
# Before
def process_tool(tool: BaseTool) -> dict:
    pass


# After (more flexible)
from aiwen.interfaces import ToolProtocol


def process_tool(tool: ToolProtocol) -> dict:
    pass
```

### Custom Registries

If you've created custom registries, consider having them implement `RegistryProtocol`:

```python
from aiwen.interfaces import RegistryProtocol
from aiwen.registries.core import BaseRegistry


# Option 1: Inherit from BaseRegistry (recommended)
class MyRegistry(BaseRegistry[str, MyType]):
    # Automatically implements RegistryProtocol
    pass


# Option 2: Implement protocol directly
class MyRegistry(RegistryProtocol[str, MyType]):
    # Must implement all protocol methods
    pass
```

## Testing

### Protocol Compliance Tests

Verify that your components satisfy protocols:

```python
import pytest
from aiwen.interfaces import ToolProtocol, is_tool


def test_my_tool_implements_protocol():
    from my_module import MyTool

    # Check protocol compliance
    assert is_tool(MyTool)

    # Or use isinstance
    tool = MyTool()
    assert isinstance(tool, ToolProtocol)


def test_protocol_attributes():
    from my_module import MyTool

    # Verify required attributes exist
    assert hasattr(MyTool, 'METADATA')
    assert hasattr(MyTool, 'InputSchema')
    assert hasattr(MyTool, 'OutputSchema')


def test_protocol_methods():
    from my_module import MyTool

    # Verify required methods exist
    tool = MyTool()
    assert callable(getattr(tool, 'execute', None))
    assert callable(getattr(tool, 'validate_input', None))
```

## Future Extensions

The protocol layer makes it easy to add new component types:

```python
# Future: Skill Protocol
@runtime_checkable
class SkillProtocol(RegistrableProtocol, Protocol):
    """Protocol for agent skills."""

    METADATA: ClassVar[SkillMetadata]

    async def activate(self, context: Any) -> None: ...
    async def deactivate(self) -> None: ...
    def get_capabilities(self) -> list[str]: ...
```

## References

- [PEP 544 – Protocols: Structural subtyping](https://peps.python.org/pep-0544/)
- [Python typing.Protocol documentation](https://docs.python.org/3/library/typing.html#typing.Protocol)
- [Mypy Protocols documentation](https://mypy.readthedocs.io/en/stable/protocols.html)
