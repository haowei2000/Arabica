"""Health check endpoints and utilities."""

import logging
from typing import Any

from aiwen.extensions.database import _engines, check_database_health

logger = logging.getLogger(__name__)


async def check_databases() -> dict[str, dict[str, Any]]:
    """
    Check health status of all database connections.

    Returns:
        Dictionary mapping database names to their health status
    """
    db_status = {}
    for bind_name in _engines:
        try:
            db_status[bind_name] = await check_database_health(bind_name)
        except Exception as e:
            logger.error("Database %s health check failed: %s", bind_name, e)
            db_status[bind_name] = {"status": "unhealthy", "error": str(e)}

    return db_status


async def check_redis() -> dict[str, Any]:
    """
    Check health status of Redis connection.

    Returns:
        Dictionary with Redis health status
    """
    try:
        from aiwen.middleware.cache_middleware import _initialized

        if _initialized:
            return {"status": "healthy"}
        return {"status": "unhealthy", "error": "Redis not initialized"}
    except Exception as e:
        logger.error("Redis health check failed: %s", e)
        return {"status": "unhealthy", "error": str(e)}


async def get_health_status() -> dict[str, Any]:
    """
    Get overall application health status.

    Returns:
        Dictionary containing:
        - status: "healthy" or "unhealthy"
        - databases: health status of each database
        - redis: health status of Redis
    """
    db_status = await check_databases()
    redis_status = await check_redis()

    # Overall status is healthy only if all components are healthy
    all_dbs_healthy = all(db["status"] == "healthy" for db in db_status.values())
    redis_healthy = redis_status["status"] == "healthy"
    overall_status = "healthy" if (all_dbs_healthy and redis_healthy) else "unhealthy"

    return {
        "status": overall_status,
        "databases": db_status,
        "redis": redis_status,
    }
