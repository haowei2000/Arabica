"""Application lifespan management for FastAPI."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from structure.core.bootstrap import bootstrap_api

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Manage application lifespan: startup and shutdown events.

    使用统一的Bootstrap模块进行初始化和清理。
    """
    logger.info("=== FastAPI Application starting up ===")

    # 使用统一的初始化流程
    bootstrap = await bootstrap_api()
    app.state.redis = bootstrap.redis_client
    logger.info("=== FastAPI Application startup complete ===")

    yield

    # 使用统一的清理流程
    logger.info("=== FastAPI Application shutting down ===")
    await bootstrap.cleanup()
    logger.info("=== FastAPI Application shutdown complete ===")
