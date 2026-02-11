"""Router registration for FastAPI application."""

import logging

from fastapi import APIRouter, FastAPI

logger = logging.getLogger(__name__)


def get_api_routers() -> list[tuple[APIRouter, str]]:
    """
    Get all API routers with their prefixes.

    Returns:
        List of tuples (router, prefix)
    """
    from aiwen.routers.executor.app import router as agents_router
    from aiwen.routers.auth.auth import router as auth_router
    from aiwen.routers.context.context import router as context_router
    from aiwen.routers.context.document import router as document_router
    from aiwen.routers.context.knowledge import router as knowledge_router
    from aiwen.routers.flush_redis import router as flush_redis_router
    from aiwen.routers.streaming import router as streaming_router

    # Unified tool management (inner + external tools + templates)
    from aiwen.routers.context.tools.tools import router as tools_router

    # User management
    from aiwen.routers.auth.users.user_examples import router as user_examples_router
    from aiwen.routers.auth.users.user_management import router as user_management_router

    # Event and run routers
    from aiwen.routers.events.event_crud import router as event_crud_router
    from aiwen.routers.runs.runs import (
        router as runs_router,
        runs_standalone_router,
    )
    from aiwen.routers.workspaces.workspace import router as workspace_router

    return [
        # Authentication & User Management
        (auth_router, "/api"),
        (user_examples_router, "/api"),
        (user_management_router, "/api"),
        # Core Features
        (flush_redis_router, "/api"),
        # Agent System
        (agents_router, "/api"),
        (streaming_router, "/api"),
        (knowledge_router, "/api/agent"),
        (context_router, "/api/agent"),
        (document_router, "/api/agent"),
        # Workspace System (event-sourced)
        (workspace_router, "/api"),
        (runs_router, "/api"),
        (runs_standalone_router, "/api"),
        # Event CRUD & Search
        (event_crud_router, "/api"),
        # Unified Tool Management
        (tools_router, "/api"),
    ]


def register_routers(app: FastAPI) -> None:
    """Register all API routers to the FastAPI app."""
    routers = get_api_routers()

    for router, prefix in routers:
        app.include_router(router, prefix=prefix)
        # Get router tags for logging
        tags = getattr(router, "tags", ["unknown"])
        logger.info("Registered router: %s (prefix: %s)", tags, prefix)

    logger.info("All routers registered successfully (%d total)", len(routers))
