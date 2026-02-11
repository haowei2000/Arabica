"""
External Tool Templates

Pre-built templates for creating ExternalTools via the API.
Each template provides a complete UserToolCreate body that users can
customize with their own URLs, parameters, and logic.

Templates come from two sources:
- **Static templates**: Hand-crafted examples (HTTP GET, POST, webhook, code, etc.)
- **Dynamic templates**: Auto-generated from registered InnerTools via ``to_template()``
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ToolTemplate(BaseModel):
    """A pre-built tool creation template"""

    id: str = Field(..., description="Unique template identifier")
    name: str = Field(..., description="Template display name")
    description: str = Field(..., description="What this template does")
    execution_mode: str = Field(..., description="Execution mode (server_run or http)")
    inner_tool_name: str | None = Field(
        None, description="InnerTool this template delegates to (for dynamic templates)"
    )
    category: str = Field(default="custom", description="Template category")
    tags: list[str] = Field(default_factory=list, description="Template tags")
    source: str = Field(
        default="static",
        description="Template source: 'static' (curated) or 'inner_tool' (auto-generated)",
    )
    template: dict[str, Any] = Field(
        ..., description="Pre-filled UserToolCreate body (ready to POST)"
    )


class ToolTemplateListResponse(BaseModel):
    """Response for listing available templates"""

    templates: list[ToolTemplate]
    total: int


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP TOOL TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

_HTTP_GET_API_TEMPLATE = ToolTemplate(
    id="http_get_api",
    name="HTTP GET API",
    description="Call an external REST API with GET method and query parameters",
    execution_mode="http",
    category="api",
    tags=["http", "get", "rest", "api"],
    template={
        "name": "my_get_api",
        "display_name": "My GET API",
        "description": "Call an external API to fetch data",
        "execution_mode": "http",
        "category": "api",
        "tags": ["http", "api"],
        "timeout": 30,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query parameter",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
        "http_config": {
            "method": "GET",
            "url": "https://api.example.com/search",
            "headers": {
                "Accept": "application/json",
                "Authorization": "Bearer YOUR_API_KEY",
            },
            "timeout": 30,
            "retry_times": 3,
            "verify_ssl": True,
        },
    },
)

_HTTP_POST_JSON_TEMPLATE = ToolTemplate(
    id="http_post_json",
    name="HTTP POST JSON API",
    description="Send JSON data to an external API with POST method",
    execution_mode="http",
    category="api",
    tags=["http", "post", "json", "rest", "api"],
    template={
        "name": "my_post_api",
        "display_name": "My POST API",
        "description": "Send data to an external API",
        "execution_mode": "http",
        "category": "api",
        "tags": ["http", "api"],
        "timeout": 30,
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Item title",
                },
                "content": {
                    "type": "string",
                    "description": "Item content",
                },
                "tags": {
                    "type": "array",
                    "description": "Tags for the item",
                    "default": [],
                },
            },
            "required": ["title", "content"],
        },
        "http_config": {
            "method": "POST",
            "url": "https://api.example.com/items",
            "headers": {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": "Bearer YOUR_API_KEY",
            },
            "timeout": 30,
            "retry_times": 3,
            "verify_ssl": True,
        },
    },
)

_HTTP_WEBHOOK_TEMPLATE = ToolTemplate(
    id="http_webhook",
    name="Webhook Notification",
    description="Send event notifications to a webhook URL (Slack, Discord, etc.)",
    execution_mode="http",
    category="notification",
    tags=["http", "webhook", "notification", "post"],
    template={
        "name": "my_webhook",
        "display_name": "My Webhook",
        "description": "Send notifications to a webhook endpoint",
        "execution_mode": "http",
        "category": "notification",
        "tags": ["webhook", "notification"],
        "timeout": 15,
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Notification message text",
                },
                "level": {
                    "type": "string",
                    "description": "Notification level: info, warning, error",
                    "default": "info",
                },
            },
            "required": ["message"],
        },
        "http_config": {
            "method": "POST",
            "url": "https://hooks.slack.com/services/YOUR/WEBHOOK/URL",
            "headers": {
                "Content-Type": "application/json",
            },
            "timeout": 15,
            "retry_times": 2,
            "verify_ssl": True,
        },
    },
)

_HTTP_REST_CRUD_TEMPLATE = ToolTemplate(
    id="http_rest_crud",
    name="REST CRUD Operation",
    description="Perform CRUD operations on a REST resource with configurable method",
    execution_mode="http",
    category="api",
    tags=["http", "rest", "crud", "api"],
    template={
        "name": "my_rest_resource",
        "display_name": "My REST Resource",
        "description": "Perform operations on a REST API resource",
        "execution_mode": "http",
        "category": "api",
        "tags": ["rest", "crud"],
        "timeout": 30,
        "input_schema": {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "description": "HTTP method: GET, POST, PUT, PATCH, DELETE",
                    "default": "GET",
                },
                "resource_id": {
                    "type": "string",
                    "description": "Resource ID (for GET/PUT/PATCH/DELETE)",
                },
                "body": {
                    "type": "object",
                    "description": "Request body (for POST/PUT/PATCH)",
                    "default": {},
                },
            },
            "required": [],
        },
        "http_config": {
            "method": "POST",
            "url": "https://api.example.com/resources",
            "headers": {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": "Bearer YOUR_API_KEY",
            },
            "timeout": 30,
            "retry_times": 3,
            "verify_ssl": True,
        },
    },
)

_HTTP_FORM_SUBMIT_TEMPLATE = ToolTemplate(
    id="http_form_submit",
    name="HTTP Form Submit",
    description="Submit form data to an external endpoint via POST",
    execution_mode="http",
    category="api",
    tags=["http", "post", "form", "submit"],
    template={
        "name": "my_form_submit",
        "display_name": "My Form Submit",
        "description": "Submit form data to an external service",
        "execution_mode": "http",
        "category": "api",
        "tags": ["form", "submit"],
        "timeout": 30,
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name field",
                },
                "email": {
                    "type": "string",
                    "description": "Email field",
                },
                "message": {
                    "type": "string",
                    "description": "Message field",
                },
            },
            "required": ["name", "email"],
        },
        "http_config": {
            "method": "POST",
            "url": "https://api.example.com/submit",
            "headers": {
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            "timeout": 30,
            "retry_times": 2,
            "verify_ssl": True,
        },
    },
)


# ═══════════════════════════════════════════════════════════════════════════════
# CODE EXECUTION TEMPLATE
# ═══════════════════════════════════════════════════════════════════════════════

_CODE_EXECUTION_TEMPLATE = ToolTemplate(
    id="code_basic",
    name="Python Code Execution",
    description="Execute custom Python code with input parameters",
    execution_mode="server_run",
    category="code",
    tags=["python", "code", "server_run"],
    template={
        "name": "my_code_tool",
        "display_name": "My Code Tool",
        "description": "Execute custom Python logic",
        "execution_mode": "server_run",
        "category": "code",
        "tags": ["python", "code"],
        "timeout": 30,
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Input text to process",
                },
            },
            "required": ["text"],
        },
        "code": (
            "# input_data is a dict with your input parameters\n"
            "text = input_data['text']\n"
            "\n"
            "# Your logic here\n"
            "processed = text.upper()\n"
            "word_count = len(text.split())\n"
            "\n"
            "# Set 'result' variable — this is returned to the caller\n"
            "result = {\n"
            "    'processed_text': processed,\n"
            "    'word_count': word_count,\n"
            "}\n"
        ),
    },
)

# ═══════════════════════════════════════════════════════════════════════════════
# CLIENT-SIDE EXECUTION TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

_CLIENT_REQUEST_TEMPLATE = ToolTemplate(
    id="client_request",
    name="Client Request",
    description="Send a request to be executed on the user's client (browser-side handler)",
    execution_mode="client_run",
    category="client",
    tags=["client", "browser", "request"],
    template={
        "name": "my_client_tool",
        "display_name": "My Client Tool",
        "description": "Execute a request on the user's client",
        "execution_mode": "client_run",
        "category": "client",
        "tags": ["client", "browser"],
        "timeout": 120,
        "input_schema": {
            "type": "object",
            "properties": {
                "handler_name": {
                    "type": "string",
                    "description": "Name of the frontend handler to invoke",
                },
                "action": {
                    "type": "string",
                    "description": "Action for the handler to perform",
                },
                "params": {
                    "type": "object",
                    "description": "Parameters to pass to the client handler",
                    "default": {},
                },
            },
            "required": ["handler_name", "action"],
        },
        "client_config": {
            "handler_name": "clientRequest",
            "config": {},
            "require_user_approval": True,
        },
    },
)


# ═══════════════════════════════════════════════════════════════════════════════
# SANDBOX EXECUTION TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

_SANDBOX_EXECUTION_TEMPLATE = ToolTemplate(
    id="sandbox_execution",
    name="Sandbox Command Execution",
    description="Execute shell commands in an isolated Docker sandbox with resource limits",
    execution_mode="container_run",
    category="code",
    tags=["sandbox", "container", "docker", "shell"],
    template={
        "name": "my_sandbox_tool",
        "display_name": "My Sandbox Tool",
        "description": "Execute a command safely in a Docker sandbox",
        "execution_mode": "container_run",
        "category": "code",
        "tags": ["sandbox", "docker"],
        "timeout": 120,
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute inside the sandbox",
                },
                "image": {
                    "type": "string",
                    "description": "Docker image to use",
                    "default": "python:3.12-slim",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Execution timeout in seconds",
                    "default": 60,
                },
                "memory_limit": {
                    "type": "string",
                    "description": "Memory limit (e.g., '256m', '1g')",
                    "default": "256m",
                },
                "network_enabled": {
                    "type": "boolean",
                    "description": "Whether to allow network access",
                    "default": False,
                },
            },
            "required": ["command"],
        },
        "container_config": {
            "image": "python:3.12-slim",
            "workdir": "/workspace",
            "resource_limits": {
                "memory": "256m",
                "cpu_quota": 50000,
                "cpu_period": 100000,
                "network_enabled": False,
                "read_only_rootfs": True,
                "pids_limit": 100,
            },
            "environment": {"PYTHONUNBUFFERED": "1"},
        },
    },
)


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPLATE REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

# All available static templates indexed by ID
TOOL_TEMPLATES: dict[str, ToolTemplate] = {
    t.id: t
    for t in [
        _HTTP_GET_API_TEMPLATE,
        _HTTP_POST_JSON_TEMPLATE,
        _HTTP_WEBHOOK_TEMPLATE,
        _HTTP_REST_CRUD_TEMPLATE,
        _HTTP_FORM_SUBMIT_TEMPLATE,
        _CODE_EXECUTION_TEMPLATE,
        _CLIENT_REQUEST_TEMPLATE,
        _SANDBOX_EXECUTION_TEMPLATE,
    ]
}


# ═══════════════════════════════════════════════════════════════════════════════
# DYNAMIC TEMPLATES (from registered InnerTools)
# ═══════════════════════════════════════════════════════════════════════════════


def get_inner_tool_templates() -> dict[str, ToolTemplate]:
    """
    Generate ToolTemplate instances from all registered InnerTools.

    Iterates over every tool in the ToolRegistry, filters for InnerTools
    (tool_type == "inner"), and calls ``to_template()`` on each to produce
    a ToolTemplate. The returned dict is keyed by template ID.

    This function is called at request time (not import time) so it always
    reflects the current state of the registry.

    Returns:
        dict[str, ToolTemplate]: Dynamic templates keyed by template ID
    """
    from aiwen.registries import ToolRegistry
    from aiwen.registries.base_class.base_tool import InnerTool

    templates: dict[str, ToolTemplate] = {}

    for tool_name in ToolRegistry.list_tools(enabled_only=False):
        tool_class = ToolRegistry.get_tool_class(tool_name)
        if tool_class is None:
            continue

        # Only generate templates from InnerTool subclasses
        if not (issubclass(tool_class, InnerTool) and tool_class is not InnerTool):
            continue

        try:
            raw = tool_class.to_template()
            template = ToolTemplate(
                id=raw["id"],
                name=raw["name"],
                description=raw["description"],
                execution_mode=raw["execution_mode"],
                inner_tool_name=raw.get("inner_tool_name"),
                category=raw.get("category", "general"),
                tags=raw.get("tags", []),
                source="inner_tool",
                template=raw["template"],
            )
            templates[template.id] = template
        except Exception:
            logger.warning(
                f"Failed to generate template from InnerTool '{tool_name}'",
                exc_info=True,
            )

    return templates


def get_all_templates(
    execution_mode: str | None = None,
    source: str | None = None,
) -> list[ToolTemplate]:
    """
    Get all templates (static + dynamic), with optional filters.

    Args:
        execution_mode: Filter by execution mode (e.g. "http", "server_run")
        source: Filter by source ("static" or "inner_tool")

    Returns:
        list[ToolTemplate]: Filtered list of templates
    """
    # Merge static and dynamic, with static taking precedence on ID collision
    all_templates: dict[str, ToolTemplate] = {}
    all_templates.update(get_inner_tool_templates())
    all_templates.update(TOOL_TEMPLATES)  # static overrides dynamic on collision

    result = list(all_templates.values())

    if execution_mode:
        result = [t for t in result if t.execution_mode == execution_mode]

    if source:
        result = [t for t in result if t.source == source]

    return result
