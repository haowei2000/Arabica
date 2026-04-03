"""Middleware configuration for FastAPI application."""

import logging
import time
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from structure.config.factory import get_settings

logger = logging.getLogger(__name__)

_SSE_PATH_SEGMENTS = ("/events/stream",)


def _is_sse_path(path: str) -> bool:
    """Return True for SSE endpoints that must not be buffered."""
    return any(seg in path for seg in _SSE_PATH_SEGMENTS)


class LoggingMiddleware:
    """Pure ASGI logging middleware.

    Unlike Starlette's ``BaseHTTPMiddleware`` (``@app.middleware("http")``),
    this does NOT wrap the response body in an intermediate pipe.  That
    pipe-based approach buffers ``StreamingResponse`` chunks, which
    breaks Server-Sent Events (SSE) entirely — the client receives nothing
    until the stream closes.

    For SSE endpoints, we pass through without any interception.  For
    regular requests, we only intercept the ``http.response.start``
    message to capture the status code for logging.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        method: str = scope.get("method", "")

        # SSE endpoints: pass through directly — zero interception.
        if _is_sse_path(path):
            logger.info("SSE stream opened: %s %s", method, path)
            await self.app(scope, receive, send)
            return

        # Regular HTTP: log timing and status code.
        start_time = time.time()
        status_code: int | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
            duration = time.time() - start_time
            log_data: dict[str, Any] = {
                "method": method,
                "path": path,
                "status_code": status_code,
                "duration_ms": round(duration * 1000, 2),
            }
            if status_code and status_code >= 500:
                logger.error("Request completed with server error: %s", log_data)
            elif status_code and status_code >= 400:
                logger.warning("Request completed with client error: %s", log_data)
            else:
                logger.info("Request completed: %s", log_data)

        except Exception as e:
            duration = time.time() - start_time
            logger.error(
                "Request failed: %s %s (%.1fms) error=%s",
                method,
                path,
                duration * 1000,
                e,
                exc_info=True,
            )
            raise


def register_middleware(app: FastAPI) -> None:
    """Register all middleware to the FastAPI app."""
    settings = get_settings()

    # CORS middleware - must be first
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure based on settings in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info("CORS middleware registered")

    # Redis cache middleware (if enabled)
    if settings.app_cache_enable:
        try:
            from structure.middleware.cache_middleware import CacheMiddleware

            app.add_middleware(
                CacheMiddleware,
                exclude_paths=[
                    "/docs",
                    "/openapi.json",
                    "/redoc",
                    "/health",
                    "/api/nl2sql/generate_sql",
                    "/events/stream",
                ],
            )
            logger.info("Cache middleware registered successfully")
        except Exception as e:
            logger.error("Failed to register cache middleware: %s", e)
    else:
        logger.info("Cache middleware is disabled in settings")

    # HTTP logging middleware — pure ASGI, does NOT buffer streaming bodies
    app.add_middleware(LoggingMiddleware)
    logger.info("Logging middleware registered")
