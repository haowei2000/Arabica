#!/usr/bin/env python3
"""
Celery Application Configuration

Configures Celery with Redis as broker for task queue management.
Uses the existing config system to get Redis connection details.
"""

import logging

from celery import Celery
from celery.signals import worker_process_init

from aiwen.config.factory import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def get_redis_url() -> str:
    """Build Redis URL from settings."""
    redis_cfg = settings.redis
    if redis_cfg.password:
        auth = f":{redis_cfg.password}@"
    else:
        auth = ""
    return f"redis://{auth}{redis_cfg.host}:{redis_cfg.port}/{redis_cfg.db}"


# Create Celery application
celery_app = Celery(
    "aiwen",
    broker=get_redis_url(),
    include=["aiwen.workers.tasks"],
)

# Celery configuration
celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # Timezone
    timezone="Asia/Shanghai",
    enable_utc=True,

    # Task settings
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
    task_soft_time_limit=3300,  # Soft limit at 55 minutes

    # Worker settings
    worker_prefetch_multiplier=1,  # Process one task at a time for long-running tasks
    worker_concurrency=4,  # Number of concurrent workers

    # Result backend (not used, we use Redis Streams for events)
    result_backend=None,

    # Task routing
    task_routes={
        "aiwen.workers.tasks.*": {"queue": "celery_agent_tasks"},
    },

    # Default queue
    task_default_queue="celery_agent_tasks",
)


@worker_process_init.connect
def init_worker_process(**kwargs):
    """
    Initialize worker process.

    Called when each worker process starts. Initializes the Agent Registry
    (in-memory only, no database sync to avoid event loop issues).
    """
    logger.info("Initializing worker process...")

    try:
        # Import agent modules to trigger @register_agent decorators
        # This populates the in-memory registry without database access
        from aiwen.services.agents.agent_registry import _import_all_agents, AgentRegistry

        logger.info("Importing agent modules...")
        _import_all_agents()

        templates = AgentRegistry.list()
        logger.info(f"Agent Registry initialized with templates: {templates}")

        if not templates:
            logger.warning("No agents registered! Check that agent modules are being imported.")

        # Initialize dimension registry (sync, no database)
        try:
            import importlib
            import aiwen.services.nl2sql.dimension_registry.dimensions
            importlib.reload(aiwen.services.nl2sql.dimension_registry.dimensions)
            logger.info("NL2SQL dimensions registered")
        except Exception as e:
            logger.warning(f"Could not initialize dimensions: {e}")

    except Exception as e:
        logger.error(f"Error initializing worker process: {e}", exc_info=True)
        raise
