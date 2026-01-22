"""
FastAPI Application Entry Point

This module initializes and configures the FastAPI application with:
- Logging configuration
- Lifespan management (startup/shutdown events)
- Middleware (CORS, caching, logging)
- Exception handlers
- API routers
- Health check endpoints
"""

import logging

from fastapi import FastAPI

from aiwen.config.factory import get_settings
from aiwen.core import (
    get_health_status,
    lifespan,
    register_exception_handlers,
    register_middleware,
    register_routers,
)
from aiwen.extensions.logger import setup_logging

# ==================== Initialize Logging ====================
# Must be called before any other imports that use logging
setup_logging()

logger = logging.getLogger(__name__)
settings = get_settings()

# ==================== Create FastAPI Application ====================
app = FastAPI(
    title="Epichust Python API",
    description="FastAPI backend for AI agent system with conversation and input management",
    version="1.0.0",
    debug=settings.DEBUG,
    lifespan=lifespan,
    # docs_url="/docs" if settings.DEBUG else None,  # Disable docs in production
    # redoc_url="/redoc" if settings.DEBUG else None,
)

# ==================== Register Middleware ====================
register_middleware(app)

# ==================== Register Exception Handlers ====================
register_exception_handlers(app)

# ==================== Register API Routers ====================
register_routers(app)

# ==================== Root and Health Check Endpoints ====================


@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with API information."""
    return {
        "input": "Epichust API is Running",
        "version": "1.0.0",
        "docs": "/docs" if settings.DEBUG else "disabled",
        "redoc": "/redoc" if settings.DEBUG else "disabled",
        "health": "/health",
        "debug": settings.DEBUG,
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """
    Comprehensive health check endpoint.

    Checks:
    - All database connections
    - Redis connection status

    Returns:
        Overall health status with details for each component
    """
    return await get_health_status()


# ==================== Application Ready ====================
logger.info("FastAPI application initialized successfully")
logger.info("Debug mode: %s", settings.DEBUG)
logger.info(
    "Documentation available at: /docs"
    if settings.DEBUG
    else "Documentation disabled in production"
)
