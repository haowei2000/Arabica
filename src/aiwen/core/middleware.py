"""Middleware configuration for FastAPI application."""

from collections.abc import Callable
import logging
import time
import traceback

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from aiwen.config.factory import get_settings

logger = logging.getLogger(__name__)


async def logging_middleware(request: Request, call_next: Callable) -> Response:
    """Log all HTTP requests with timing and status information."""
    start_time = time.time()
    log_data = {
        "method": request.method,
        "path": request.url.path,
        "query": str(dict(request.query_params)),
        "client_ip": request.client.host if request.client else "unknown",
    }

    try:
        response = await call_next(request)
        duration = time.time() - start_time
        log_data.update(
            {
                "status_code": response.status_code,
                "duration_ms": round(duration * 1000, 2),
            }
        )

        # Log level based on status code
        if response.status_code >= 500:
            logger.error("Request completed with server error: %s", log_data)
        elif response.status_code >= 400:
            logger.warning("Request completed with client error: %s", log_data)
        else:
            logger.info("Request completed: %s", log_data)

        return response
    except Exception as e:
        duration = time.time() - start_time
        log_data.update(
            {
                "status_code": 500,
                "duration_ms": round(duration * 1000, 2),
                "error": str(e),
                "traceback": traceback.format_exc(),
            }
        )
        logger.error("Request failed: %s", log_data)
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
            from aiwen.middleware.cache_middleware import CacheMiddleware

            app.add_middleware(
                CacheMiddleware,
                exclude_paths=[
                    "/docs",
                    "/openapi.json",
                    "/redoc",
                    "/health",
                    "/api/nl2sql/generate_sql",
                ],
            )
            logger.info("Cache middleware registered successfully")
        except Exception as e:
            logger.error("Failed to register cache middleware: %s", e)
    else:
        logger.info("Cache middleware is disabled in settings")

    # HTTP logging middleware
    app.middleware("http")(logging_middleware)
    logger.info("Logging middleware registered")
