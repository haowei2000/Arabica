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

__all__ = [
    # Protocols
    "RegistrableProtocol",
    "ToolProtocol",
    "ExecutorProtocol",
    "RegistryProtocol",
    # Type Checkers
    "is_tool",
    "is_executor",
    "is_registry",
    # Metadata
    "PROTOCOL_REGISTRY",
]
