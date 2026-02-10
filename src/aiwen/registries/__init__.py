"""
Centralized Registry System for Aiwen Service.

This module provides a unified registry architecture for managing different
types of components (tools, agents, models, etc.) with consistent patterns
for registration, discovery, and lifecycle management.

Available Registries:
    - ToolRegistry: Manages tool instances and schemas
    - ExecutorRegistry: Manages agent executor templates
    - ModelRegistry: Manages LLM model configurations (future)
    - SkillRegistry: Manages agent skills (future)

Usage:
    from aiwen.registries import get_registry, ToolRegistry, register_tool

    # Register component
    @register_tool
    class MyTool(BaseTool):
        ...

    # Get registry singleton
    tool_registry = get_registry(ToolRegistry)

    # Access tools
    tool = tool_registry.get_instance("my_tool")
"""

# Import from unified core module
from aiwen.registries.core import (
    BaseRegistry,
    ExecutorRegistry,
    RegistryConfig,
    ToolRegistry,
    register_executor,
    register_tool,
)

# Import manager
from aiwen.registries.manager import (
    RegistryManager,
    get_registry,
    register_registry,
    sync_all_registries,
)

__all__ = [
    # Base classes
    "BaseRegistry",
    "RegistryConfig",
    # Manager
    "RegistryManager",
    "get_registry",
    "register_registry",
    "sync_all_registries",
    # Registries
    "ToolRegistry",
    "ExecutorRegistry",
    # Decorators
    "register_tool",
    "register_executor",
]
