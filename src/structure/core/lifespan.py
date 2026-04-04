"""Application lifespan management for FastAPI."""

import asyncio
from collections.abc import AsyncGenerator
import contextlib
from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI

from structure.core.bootstrap import bootstrap_api

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Manage application lifespan: startup and shutdown events.

    使用统一的Bootstrap模块进行初始化和清理。

    When EMBED_WORKER=true (default), the event worker runs as a background
    asyncio task inside this process, sharing imports and DB/Redis connections.
    """
    logger.info("=== FastAPI Application starting up ===")

    bootstrap = await bootstrap_api()
    app.state.redis = bootstrap.redis_client

    worker_task: asyncio.Task | None = None
    if os.environ.get("EMBED_WORKER", "true").lower() == "true":
        from structure.worker_cli import run_workers_embedded

        worker_task = asyncio.create_task(
            run_workers_embedded(bootstrap.redis_client),
            name="embedded-event-worker",
        )
        logger.info("=== Embedded event worker started ===")

    logger.info("=== FastAPI Application startup complete ===")

    yield

    logger.info("=== FastAPI Application shutting down ===")

    if worker_task and not worker_task.done():
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task

    await bootstrap.cleanup()
    logger.info("=== FastAPI Application shutdown complete ===")
