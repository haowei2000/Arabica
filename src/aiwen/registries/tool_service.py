"""Concrete implementations of ToolProvider / ToolCaller protocols.

These adapters bridge the abstract protocols defined in
``core.interfaces.tool_service`` to the concrete ``ToolRegistry``.

The Worker injects these into the executor config so the executor never
touches the ToolRegistry singleton directly.
"""

from __future__ import annotations

import logging
from typing import Any

from aiwen.core.interfaces import ToolCaller, ToolProvider
from aiwen.core.interfaces.tool import BaseTool

logger = logging.getLogger(__name__)


class RegistryToolProvider(ToolProvider):
    """Provides tool classes/instances from the ToolRegistry.

    Aggregates tools from:
      1. Explicit ``extra_tool_classes`` (e.g. browser tools, server tools)
         passed at construction time.
      2. All tools registered in the ``ToolRegistry`` singleton.

    This keeps the executor unaware of *where* tools originate.
    """

    def __init__(
        self,
        extra_tool_classes: list[type[BaseTool]] | None = None,
    ) -> None:
        self._extra_tool_classes: list[type[BaseTool]] = extra_tool_classes or []

    def get_tool_classes(self) -> list[type[BaseTool]]:  # ty:ignore[invalid-method-override]
        """Return extra + registry tool classes."""
        from aiwen.registries.core import ToolRegistry

        classes: list[type[BaseTool]] = list(self._extra_tool_classes)

        # Avoid duplicates: collect names already present
        known_names = {cls.METADATA.name for cls in classes}

        for tool_name in ToolRegistry.list_tools():
            if tool_name in known_names:
                continue
            tool_cls = ToolRegistry.get_tool_class(tool_name)
            if tool_cls is not None:
                classes.append(tool_cls)
                known_names.add(tool_name)

        return classes

    def get_tool_instance(self, tool_name: str) -> BaseTool | None:
        """Return a tool instance by name."""
        from aiwen.registries.core import ToolRegistry

        return ToolRegistry.get_tool_instance(tool_name)


class RegistryToolCaller(ToolCaller):
    """Executes tools via the ToolRegistry singleton.

    Looks up the tool instance by name, then calls it with the
    provided arguments using ``BaseTool.__call__()``.

    An optional ``extra_instances`` dict allows per-run tools
    (e.g. user-defined ExternalTools loaded from the database)
    to be resolved without polluting the global ToolRegistry.
    Extra instances take precedence over registry look-ups.
    """

    def __init__(
        self,
        extra_instances: dict[str, BaseTool] | None = None,
    ) -> None:
        self._extra_instances: dict[str, BaseTool] = extra_instances or {}

    async def call(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a tool through extra instances or the registry."""
        # Per-run extra instances (user-defined tools) checked first
        if tool_name in self._extra_instances:
            return await self._extra_instances[tool_name](**arguments)

        from aiwen.registries.core import ToolRegistry

        tool_instance = ToolRegistry.get_tool_instance(tool_name)
        if tool_instance is not None:
            return await tool_instance(**arguments)

        raise ValueError(f"Tool '{tool_name}' not found in ToolRegistry")
