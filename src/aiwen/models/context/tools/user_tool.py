"""Backward-compatibility alias.

The UserTool table has been merged into the unified Tool table.
This module re-exports Tool as UserTool so existing imports continue to work.
"""

from aiwen.models.context.tools.tool import Tool as UserTool

__all__ = ["UserTool"]
