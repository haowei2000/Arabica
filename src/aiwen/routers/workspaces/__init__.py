# aiwen/routers/workspaces/__init__.py
"""Workspace routers package."""

from aiwen.routers.workspaces.events import router as events_router
from aiwen.routers.workspaces.runs import router as runs_router
from aiwen.routers.workspaces.workspace import router as workspace_router

__all__ = [
    "events_router",
    "runs_router",
    "workspace_router",
]
