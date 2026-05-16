"""Core application modules for FastAPI configuration."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = [
    "get_health_status",
    "lifespan",
    "register_exception_handlers",
    "register_middleware",
    "register_routers",
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Proxy the FastAPI lifespan without importing bootstrap at package load."""
    from structure.core.lifespan import lifespan as app_lifespan

    async with app_lifespan(app):
        yield


def __getattr__(name: str) -> Any:
    """Lazily expose core helpers without importing app lifespan at package load."""
    if name == "get_health_status":
        from structure.core.health import get_health_status

        return get_health_status
    if name == "register_exception_handlers":
        from structure.core.exceptions import register_exception_handlers

        return register_exception_handlers
    if name == "register_middleware":
        from structure.core.middleware import register_middleware

        return register_middleware
    if name == "register_routers":
        from structure.core.routers import register_routers

        return register_routers
    raise AttributeError(f"module 'structure.core' has no attribute {name!r}")
