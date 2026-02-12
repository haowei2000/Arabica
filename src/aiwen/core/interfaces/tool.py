"""
Tool Interface

Re-exports tool-related classes from registries for backward compatibility
and centralized access.
"""

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
    # Base Classes
    "BaseTool",
    "InnerTool",
    "ExternalTool",
    # Schemas
    "ToolInputSchema",
    "ToolOutputSchema",
    "ToolMetadata",
    # Configs
    "HTTPConfig",
    "CeleryConfig",
    "ContainerConfig",
    "ClientConfig",
    "ResourceLimits",
]
