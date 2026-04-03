"""Concrete implementations of ToolProvider / ToolCaller protocols.

These adapters bridge the abstract protocols defined in
``core.interfaces.tool_service`` to the concrete ``ToolRegistry``.

The Worker injects these into the executor config so the executor never
touches the ToolRegistry singleton directly.
"""

from __future__ import annotations

import logging
from typing import Any

from structure.core.interfaces import ToolCaller, ToolProvider
from structure.core.interfaces.tool import BaseTool

logger = logging.getLogger(__name__)


class RegistryToolProvider(ToolProvider):
    """Provides tool classes imported from MCP servers.

    Only serves tools explicitly passed as ``extra_tool_classes`` — i.e.
    tools imported from MCP servers via the tool library.  Inner (built-in)
    tools are not exposed here; they must be imported from the structure-mcp
    server like any other MCP server.
    """

    def __init__(
        self,
        extra_tool_classes: list[type[BaseTool]] | None = None,
    ) -> None:
        self._extra_tool_classes: list[type[BaseTool]] = extra_tool_classes or []

    def get_tool_classes(self) -> list[type[BaseTool]]:
        """Return MCP-imported tool classes only."""
        return list(self._extra_tool_classes)

    def get_tool_instance(self, tool_name: str) -> BaseTool | None:
        """Return a tool instance by name from MCP-imported tools."""
        for cls in self._extra_tool_classes:
            if cls.METADATA.name == tool_name:
                return cls()
        return None


class RegistryToolCaller(ToolCaller):
    """Executes MCP-imported tools by name.

    Only resolves tools from ``extra_instances`` — tools that were imported
    from MCP servers and loaded via ``DynamicToolLoader``.  Inner (built-in)
    tools are not callable here; they must be imported from the structure-mcp
    server first.
    """

    def __init__(
        self,
        extra_instances: dict[str, BaseTool] | None = None,
    ) -> None:
        self._extra_instances: dict[str, BaseTool] = extra_instances or {}

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute an MCP-imported tool by name."""
        if tool_name in self._extra_instances:
            return await self._extra_instances[tool_name](**arguments)

        raise ValueError(
            f"Tool '{tool_name}' not found. "
            "Import it from an MCP server first (including the structure-mcp server for built-in tools)."
        )
