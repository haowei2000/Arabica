"""MCP Loader — utilities for probing MCP servers and building tool classes.

``probe_mcp_server(config)``
    Connect to an MCP server, list its tools, and return the raw metadata.
    Used by the API endpoint to preview what a server exposes before import.

``build_mcp_tool_class(record)``
    Create a dynamic ``InnerTool`` subclass from a ``Tool`` DB record whose
    ``tool_type == "mcp"``.  The subclass's ``execute()`` reuses a global
    FastMCP ``Client`` per unique server config (lazy-connect, auto-reconnect).

Both helpers are called by ``DynamicToolLoader`` (for runtime loading) and
by the tools router (for the probe / import-from-mcp endpoints).

MCP config stored in ``Tool.config``::

    {
        "mcp_transport": "sse" | "stdio",
        "mcp_url":       "http://host/mcp",    # SSE only
        "mcp_command":   "uvx",                # stdio only
        "mcp_args":      ["mcp-server-github"],# stdio only
        "mcp_env":       {"TOKEN": "xxx"},     # stdio only
        "mcp_tool_name": "create_issue"
    }
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global client cache  (config_key → entered Client instance)
# ---------------------------------------------------------------------------

_client_cache: dict[str, Any] = {}


def _config_key(client_config: str | dict) -> str:
    if isinstance(client_config, dict):
        return json.dumps(client_config, sort_keys=True)
    return client_config


async def _get_client(client_config: str | dict) -> Any:
    """Return a shared, already-entered FastMCP Client for *client_config*.

    Creates and enters the client on the first call for a given config.
    If the cached client has become disconnected, clears it and reconnects.
    """
    from fastmcp import Client

    key = _config_key(client_config)
    client = _client_cache.get(key)

    if client is not None:
        # Quick liveness check — if the transport is closed, reconnect.
        try:
            if not client.is_connected():
                logger.debug("MCP client for '%s' disconnected, reconnecting", key)
                client = None
        except Exception:
            client = None

    if client is None:
        client = Client(client_config)
        await client.__aenter__()
        _client_cache[key] = client
        logger.debug("MCP client connected for '%s'", key)

    return client


async def close_all_clients() -> None:
    """Close all cached MCP clients (call on worker shutdown)."""
    for key, client in list(_client_cache.items()):
        try:
            await client.__aexit__(None, None, None)
        except Exception as exc:
            logger.debug("Error closing MCP client '%s': %s", key, exc)
        del _client_cache[key]


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def client_config_from_tool_config(cfg: dict[str, Any]) -> str | dict:
    """Build the FastMCP ``Client`` constructor argument from a tool config dict."""
    transport = cfg.get("mcp_transport", "sse")
    if transport == "stdio":
        result: dict = {"command": cfg["mcp_command"]}
        if cfg.get("mcp_args"):
            result["args"] = cfg["mcp_args"]
        if cfg.get("mcp_env"):
            result["env"] = cfg["mcp_env"]
        return result
    return cfg.get("mcp_url", "")


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

async def probe_mcp_server(client_config: str | dict) -> list[dict[str, Any]]:
    """Connect to an MCP server and return its tool list as plain dicts.

    Args:
        client_config: URL string (SSE) or stdio dict passed to ``fastmcp.Client``.

    Returns:
        List of ``{"name", "description", "input_schema"}`` dicts.

    Raises:
        Exception: propagated from FastMCP if the connection fails.
    """
    from fastmcp import Client

    async with Client(client_config) as client:
        mcp_tools = await client.list_tools()

    return [
        {
            "name": t.name,
            "description": t.description or "",
            "input_schema": getattr(t, "inputSchema", None),
        }
        for t in mcp_tools
    ]


# ---------------------------------------------------------------------------
# Dynamic class builder
# ---------------------------------------------------------------------------

def _json_type_to_python(json_type: str | list) -> type:
    _MAP = {"string": str, "integer": int, "number": float,
            "boolean": bool, "array": list, "object": dict}
    if isinstance(json_type, list):
        non_null = [t for t in json_type if t != "null"]
        base = _MAP.get(non_null[0], Any) if non_null else Any
        return base | None if "null" in json_type else base  # type: ignore[return-value]
    return _MAP.get(json_type, Any)


def _build_input_schema(json_schema: dict[str, Any] | None) -> type[ToolInputSchema]:
    from pydantic import Field, create_model

    if not json_schema:
        return ToolInputSchema
    properties = json_schema.get("properties", {})
    required_fields = set(json_schema.get("required", []))
    if not properties:
        return ToolInputSchema

    fields: dict[str, Any] = {}
    for name, prop in properties.items():
        ft = _json_type_to_python(prop.get("type", "string"))
        desc = prop.get("description", "")
        default = prop.get("default", ...)
        if name in required_fields:
            fields[name] = (ft, Field(description=desc))
        else:
            if default is ...:
                default = None
                ft = ft | None  # type: ignore[assignment]
            fields[name] = (ft, Field(default=default, description=desc))

    return create_model("MCPInput", __base__=ToolInputSchema, **fields)


def build_mcp_tool_class(record: Any) -> type[InnerTool]:
    """Create a dynamic ``InnerTool`` subclass from a Tool DB record (tool_type='mcp').

    The subclass's ``execute()`` reuses a global FastMCP Client per server,
    reconnecting automatically if the connection has dropped.

    Args:
        record: A ``Tool`` ORM instance with ``tool_type == "mcp"`` and a
                populated ``config`` dict.
    """
    cfg: dict[str, Any] = record.config or {}
    mcp_tool_name: str = cfg.get("mcp_tool_name") or record.name
    client_config = client_config_from_tool_config(cfg)
    input_schema_cls = _build_input_schema(record.input_schema)

    _client_config = client_config
    _mcp_tool_name = mcp_tool_name

    class _MCPTool(InnerTool):
        METADATA = ToolMetadata(
            name=record.name,
            display_name=record.display_name or record.name,
            description=record.description or "",
            category=record.category or "mcp",
            tags=(record.tags or []) + ["mcp"],
            timeout=record.timeout or 30,
        )
        InputSchema = input_schema_cls

        async def execute(self, input_data: ToolInputSchema) -> ToolOutputSchema:  # type: ignore[override]
            arguments = input_data.model_dump(exclude_none=True)
            try:
                client = await _get_client(_client_config)
                result = await client.call_tool(_mcp_tool_name, arguments)
            except Exception as exc:
                # Connection may have died mid-call; evict cache so next call reconnects.
                _client_cache.pop(_config_key(_client_config), None)
                logger.error("MCP tool '%s' failed: %s", _mcp_tool_name, exc)
                return ToolOutputSchema(success=False, error=str(exc))

            if isinstance(result, list):
                texts, data_items = [], []
                for item in result:
                    if hasattr(item, "text"):
                        texts.append(item.text)
                    elif hasattr(item, "model_dump"):
                        data_items.append(item.model_dump())
                    else:
                        data_items.append(str(item))
                return ToolOutputSchema(
                    success=True,
                    message="\n".join(texts) if texts else None,
                    data={"results": data_items} if data_items else None,
                )
            return ToolOutputSchema(success=True, data={"result": result})

    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in record.name)
    _MCPTool.__name__ = f"MCPTool_{safe}"
    _MCPTool.__qualname__ = f"MCPTool_{safe}"
    return _MCPTool
