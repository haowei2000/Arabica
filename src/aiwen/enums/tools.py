"""Tool-related enum definitions."""

from enum import Enum


class ToolExecutionMode(str, Enum):
    """Tool execution modes."""

    HTTP = "http"  # HTTP API call
    SERVER_RUN = "server_run"  # Server-side direct execution
    CLIENT_RUN = "client_run"  # Client-side execution
    CONTAINER_RUN = "container_run"  # Container isolated execution
    CELERY_RUN = "celery_run"  # Celery async task


class AllowedToolType(str, Enum):
    """Allowed tool types for API creation.

    Only 'external' tools can be created via the API.
    'inner' tools are code-defined and cannot be created through the API.
    """

    EXTERNAL = "external"
