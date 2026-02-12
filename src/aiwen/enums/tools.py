"""Tool-related enum definitions."""

from enum import Enum


class AllowedToolType(str, Enum):
    """Allowed tool types for API creation.

    Only 'external' tools can be created via the API.
    'inner' tools are code-defined and cannot be created through the API.
    """

    EXTERNAL = "external"
