import logging

from fastmcp import FastMCP

from structure.core.interfaces.tool import InnerTool
from structure.registries.core import ToolRegistry

logger = logging.getLogger("mcp.inner_tools")


def register_inner_tools_to_mcp(mcp: FastMCP):
    """
    Discover all InnerTools and register them to the given FastMCP instance.
    """
    registry = ToolRegistry._get_singleton_instance()
    # Ensure tools are discovered
    registry.discover_and_register_tools()

    tool_classes = registry.list_tool_classes(enabled_only=True)

    for tool_cls in tool_classes:
        if not issubclass(tool_cls, InnerTool):
            continue

        metadata = tool_cls.METADATA
        name = metadata.name
        description = metadata.description or f"Inner tool: {name}"  # noqa: F841

        # Create a wrapper function for FastMCP
        # FastMCP uses inspection to determine tool arguments

        try:
            # We'll use a helper to register it
            _register_single_tool(mcp, tool_cls)
            logger.info(f"Registered inner tool '{name}' to MCP")
        except Exception as e:
            logger.error(f"Failed to register inner tool '{name}' to MCP: {e}")


def _register_single_tool(mcp: FastMCP, tool_cls):
    """Register a single InnerTool class to FastMCP."""
    metadata = tool_cls.METADATA
    name = metadata.name
    description = metadata.description or name

    # We can use mcp.add_tool() if available, or just use the decorator
    # FastMCP typically expects a function where arguments have type hints.

    # Let's look at how FastMCP registers tools.
    # If we can't easily dynamic-hint, we might need a more generic tool.

    @mcp.tool(name=name, description=description)
    async def inner_tool_executor(arguments: dict) -> str:
        # Generic executor that takes a dict
        # Note: This might not be ideal as it doesn't expose the schema to MCP clients properly
        # unless we tell FastMCP about the schema.
        instance = tool_cls()
        input_data = tool_cls.InputSchema(**arguments)
        result = await instance.execute(input_data)
        if result.success:
            return str(result.data) if result.data is not None else result.message
        return f"Error: {result.error or result.message}"
