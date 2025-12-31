"""Router registration for FastAPI application."""

import logging
from typing import List, Tuple

from fastapi import APIRouter, FastAPI

logger = logging.getLogger(__name__)


def get_api_routers() -> list[tuple[APIRouter, str]]:
    """
    Get all API routers with their prefixes.

    Returns:
        List of tuples (router, prefix)
    """
    from aiwen.routers.agents.app import router as agents_router
    from aiwen.routers.agents.chat import router as chat_router
    from aiwen.routers.agents.conversations import router as conversations_router
    from aiwen.routers.agents.messages import router as messages_router
    from aiwen.routers.auth import router as auth_router
    from aiwen.routers.files import router as files_router
    from aiwen.routers.flush_redis import router as flush_redis_router
    from aiwen.routers.nl2sql import router as nl2sql_router
    from aiwen.routers.test_auth import router as test_auth_router
    from aiwen.routers.user_examples import router as user_examples_router
    from aiwen.routers.user_management import router as user_management_router

    return [
        # Authentication & User Management
        (auth_router, "/api"),
        (test_auth_router, "/api"),
        (user_examples_router, "/api"),
        (user_management_router, "/api"),

        # Core Features
        (nl2sql_router, "/api"),
        (files_router, "/api"),
        (flush_redis_router, "/api"),

        # Agent System
        (agents_router, "/api"),
        (chat_router, "/api"),
        (conversations_router, "/api"),
        (messages_router, "/api"),
    ]


def register_routers(app: FastAPI) -> None:
    """Register all API routers to the FastAPI app."""
    routers = get_api_routers()

    for router, prefix in routers:
        app.include_router(router, prefix=prefix)
        # Get router tags for logging
        tags = getattr(router, 'tags', ['unknown'])
        logger.info("Registered router: %s (prefix: %s)", tags, prefix)

    logger.info("All routers registered successfully (%d total)", len(routers))
