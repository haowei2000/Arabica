"""
Base Classes for Registry Components

Provides base classes that all registry components should inherit from or implement.
"""

from aiwen.registries.base_class.base_executor import (
    AgentEvent,
    Executor,
    WaitingForTool,
)
from aiwen.registries.base_class.base_tool import (
    BaseTool,
    CeleryConfig,
    ClientConfig,
    ContainerConfig,
    ExternalTool,
    HTTPConfig,
    InnerTool,
    ResourceLimits,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)

__all__ = [
    # Executor
    "Executor",
    "AgentEvent",
    "WaitingForTool",
    # Tools
    "BaseTool",
    "InnerTool",
    "ExternalTool",
    "ToolInputSchema",
    "ToolOutputSchema",
    "ToolMetadata",
    "HTTPConfig",
    "CeleryConfig",
    "ContainerConfig",
    "ClientConfig",
    "ResourceLimits",
]
