"""
Core Interfaces Module

Provides protocol definitions and base interfaces for registry components.
"""

from aiwen.core.interfaces.protocols import (
    ExecutorProtocol,
    RegistrableProtocol,
    RegistryProtocol,
    ToolProtocol,
    is_executor,
    is_registry,
    is_tool,
    PROTOCOL_REGISTRY,
)
from aiwen.core.interfaces.tool_service import (
    ToolCaller,
    ToolProvider,
)

__all__ = [
    # Protocols
    "RegistrableProtocol",
    "ToolProtocol",
    "ExecutorProtocol",
    "RegistryProtocol",
    # Tool Service Abstractions (DIP)
    "ToolProvider",
    "ToolCaller",
    # Type Checkers
    "is_tool",
    "is_executor",
    "is_registry",
    # Metadata
    "PROTOCOL_REGISTRY",
]
