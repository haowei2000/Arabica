"""Tool Service Abstractions (Dependency Inversion)

Defines protocols that decouple executors from the concrete ToolRegistry.

High-level modules (DefaultExecutor) depend on these abstractions:
  - ToolProvider: supplies available tool classes/instances
  - ToolCaller:   executes a named tool with arguments

Low-level modules (RegistryToolProvider, RegistryToolCaller) implement them.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ToolProvider(Protocol):
    """Provides available tool definitions to an executor.

    Implementations decide *where* tools come from (registry, config,
    external service, etc.).  The executor only sees this interface.
    """

    @abstractmethod
    def get_tool_classes(self) -> list[type]:
        """Return all available tool classes.

        Each class must expose ``METADATA`` (with ``.name``,
        ``.description``) and ``InputSchema`` – the same contract as
        ``BaseTool`` subclasses.
        """
        ...

    @abstractmethod
    def get_tool_instance(self, tool_name: str) -> Any | None:
        """Return a cached instance of a tool by name.

        Returns ``None`` if the tool is not found.
        """
        ...


@runtime_checkable
class ToolCaller(Protocol):
    """Executes a tool by name with the given arguments.

    Implementations decide *how* the tool is invoked (direct call,
    RPC, sandbox, etc.).  The executor only sees this interface.
    """

    @abstractmethod
    async def call(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a tool and return its result.

        Args:
            tool_name: Name of the tool (matches ``METADATA.name``).
            arguments: Arguments to pass to the tool.

        Returns:
            Tool execution result dict.

        Raises:
            ValueError: If the tool is not found.
        """
        ...
