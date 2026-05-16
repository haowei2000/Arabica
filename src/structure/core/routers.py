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
    from structure.routers.auth.auth import router as auth_router
    from structure.routers.auth.friends import router as friends_router
    from structure.routers.auth.quota import router as quota_router

    # User management
    from structure.routers.auth.users.user_examples import (
        router as user_examples_router,
    )
    from structure.routers.auth.users.user_management import (
        router as user_management_router,
    )
    from structure.routers.context.context import router as context_router
    from structure.routers.context.document import router as document_router
    from structure.routers.context.knowledge import router as knowledge_router
    from structure.routers.context.skills import router as skills_router
    from structure.routers.context.tools.bundles import router as tool_bundles_router

    # Unified tool management (inner + external tools + templates)
    from structure.routers.context.tools.tools import router as tools_router

    # Event and run routers
    from structure.routers.events.event_crud import router as event_crud_router
    from structure.routers.executor.app import router as agents_router
    from structure.routers.flush_redis import router as flush_redis_router
    from structure.routers.llm.chat_models import router as chat_models_router
    from structure.routers.llm.embedding_models import router as embedding_models_router
    from structure.routers.runs.artifacts import router as artifacts_router
    from structure.routers.runs.runs import (
        router as runs_router,
        runs_standalone_router,
    )
    from structure.routers.runs.tasks import router as tasks_router
    from structure.routers.streaming import router as streaming_router
    from structure.routers.triggers import router as triggers_router
    from structure.routers.user_triggers import router as user_triggers_router
    from structure.routers.workspaces.chat_files import router as chat_files_router
    from structure.routers.workspaces.workspace import router as workspace_router

    return [
        # Authentication & User Management
        (auth_router, "/api"),
        (user_examples_router, "/api"),
        (user_management_router, "/api"),
        (friends_router, "/api"),
        (quota_router, "/api"),
        # Core Features
        (flush_redis_router, "/api"),
        # Agent System
        (agents_router, "/api"),
        (streaming_router, "/api"),
        (knowledge_router, "/api/agent"),
        (context_router, "/api/agent"),
        (document_router, "/api/agent"),
        (skills_router, "/api/agent"),
        # Workspace System (event-sourced)
        (workspace_router, "/api"),
        (chat_files_router, "/api"),
        (triggers_router, "/api"),
        (user_triggers_router, "/api"),
        (runs_router, "/api"),
        (runs_standalone_router, "/api"),
        # Tasks & Artifacts
        (tasks_router, "/api"),
        (artifacts_router, "/api"),
        # Event CRUD & Search
        (event_crud_router, "/api"),
        # Unified Tool Management
        (tools_router, "/api"),
        (tool_bundles_router, "/api"),
        # LLM Model Management
        (chat_models_router, "/api"),
        (embedding_models_router, "/api"),
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
