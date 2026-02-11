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

Protocol Layer:
    The system uses structural protocols (PEP 544) to define component interfaces:
    - ToolProtocol: Interface for tool components
    - ExecutorProtocol: Interface for executor components
    - RegistryProtocol: Interface for registry implementations
    - RegistrableProtocol: Base interface for all registrable components

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

    # Type checking with protocols
    from aiwen.registries import ToolProtocol, is_tool
    if is_tool(my_component):
        print("Valid tool!")
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

# Import protocols
from aiwen.core.interfaces import (
    ExecutorProtocol,
    RegistrableProtocol,
    RegistryProtocol,
    ToolProtocol,
    is_executor,
    is_registry,
    is_tool,
    PROTOCOL_REGISTRY,
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
    # Protocols
    "RegistrableProtocol",
    "ToolProtocol",
    "ExecutorProtocol",
    "RegistryProtocol",
    # Type Checkers
    "is_tool",
    "is_executor",
    "is_registry",
    # Protocol Metadata
    "PROTOCOL_REGISTRY",
]
