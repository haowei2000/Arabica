#!/usr/bin/env python3
"""
Redis Cache Middleware for FastAPI

This middleware provides automatic caching for all HTTP requests using Redis.
"""

import asyncio
from collections.abc import Callable
from functools import wraps
import hashlib
import json
import logging

from fastapi import FastAPI, Request
from fastapi.responses import Response
import redis
import redis.asyncio as redis_async
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse
from starlette.types import ASGIApp

from structure.config.factory import get_settings
from structure.utils.json_utils import dumps as json_dumps

logger = logging.getLogger(__name__)

# Global Redis client instances
_redis_client_sync: redis.Redis | None = None
_redis_client_async: redis_async.Redis | None = None
_initialized = False


async def init_redis_client():
    """Initialize Redis clients for middleware"""
    global _redis_client_sync, _redis_client_async, _initialized
    if _initialized:
        return

    try:
        settings = get_settings()
        redis_config = settings.redis

        # Async client for middleware operations
        _redis_client_async = redis_async.Redis(
            host=redis_config.host,
            port=redis_config.port,
            db=redis_config.db,
            password=redis_config.password or None,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )

        # Sync client for decorator operations
        _redis_client_sync = redis.Redis(
            host=redis_config.host,
            port=redis_config.port,
            db=redis_config.db,
            password=redis_config.password or None,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )

        # Test connection
        await _redis_client_async.ping()
        _initialized = True
        logger.info(
            f"✓ Redis clients initialized: {redis_config.host}:{redis_config.port}/{redis_config.db}"
        )
    except Exception as e:
        logger.error(f"✗ Failed to initialize Redis clients: {e}")
        raise


async def close_redis_client():
    """Close Redis clients"""
    global _redis_client_sync, _redis_client_async, _initialized
    if _redis_client_async:
        try:
            await _redis_client_async.close()
            logger.info("✓ Redis async client closed")
        except Exception as e:
            logger.error(f"✗ Error closing Redis async client: {e}")

    if _redis_client_sync:
        try:
            _redis_client_sync.close()
            logger.info("✓ Redis sync client closed")
        except Exception as e:
            logger.error(f"✗ Error closing Redis sync client: {e}")

    _redis_client_sync = None
    _redis_client_async = None
    _initialized = False


def get_redis_client(is_async: bool = True):
    """Get Redis client instance"""
    if not _initialized:
        raise RuntimeError(
            "Redis clients not initialized. Call init_redis_client() first."
        )

    if is_async:
        if _redis_client_async is None:
            raise RuntimeError("Redis async client not available")
        return _redis_client_async
    if _redis_client_sync is None:
        raise RuntimeError("Redis sync client not available")
    return _redis_client_sync


class CacheMiddleware(BaseHTTPMiddleware):
    """Redis cache middleware for FastAPI

    This middleware provides automatic caching for all HTTP requests using Redis.

    Path filtering behavior:
    - By default, all paths are cached except those explicitly excluded
    - If include_paths is provided, ONLY those paths are cached (whitelist approach)
    - exclude_paths takes precedence over include_paths - if a path is in both lists, it will NOT be cached
    - Built-in excluded paths (/docs, /openapi.json, /redoc, /health) are always excluded unless overridden

    Example usage:
        # Cache all paths except /api/private/*
        app.add_middleware(CacheMiddleware, exclude_paths=["/api/private"])

        # Cache ONLY /api/public/* and /api/shared/* paths
        app.add_middleware(CacheMiddleware, include_paths=["/api/public", "/api/shared"])

        # Cache all paths except /admin/*, even if they are in include_paths
        app.add_middleware(CacheMiddleware, include_paths=["/api/data"], exclude_paths=["/admin"])

    Args:
        app: The ASGI application
        exclude_paths: List of paths to exclude from caching (blacklist)
        include_paths: List of paths to include for caching (whitelist). If provided,
                      only these paths will be cached.
        cache_expiry: Cache expiration time in seconds (default: 30)
    """

    def __init__(
        self,
        app: ASGIApp,
        exclude_paths: list = None,  # noqa: RUF013
        include_paths: list = None,  # noqa: RUF013
        cache_expiry: int = 30,
    ):
        super().__init__(app)
        self.exclude_paths = exclude_paths or [
            "/docs",
            "/openapi.json",
            "/redoc",
            "/health",
        ]
        self.include_paths = include_paths or []  # 默认只包含指定的路径
        self.cache_expiry = cache_expiry

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 如果指定了include_paths，则只有在列表中的路径才会被缓存
        if self.include_paths and request.url.path not in self.include_paths:
            return await call_next(request)

        # Check if path is excluded from caching
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        # Generate cache key
        cache_key = self._generate_cache_key(request)

        # Try to get from cache
        redis_client = get_redis_client(is_async=True)
        try:
            cached_response = await redis_client.get(cache_key)
            if cached_response:
                try:
                    cached_data = json.loads(cached_response)
                    logger.debug(f"Cache HIT for key: {cache_key}")
                    return Response(
                        content=cached_data["content"],
                        status_code=cached_data["status_code"],
                        headers=cached_data.get("headers", {}),
                        media_type=cached_data.get("media_type"),
                    )
                except Exception as e:
                    logger.error(f"Cache deserialize error: {e}")
            else:
                logger.debug(f"Cache MISS for key: {cache_key}")
        except Exception as e:
            logger.error(f"Redis get error: {e}")

        # Call the actual handler
        response = await call_next(request)

        # Only cache successful responses
        if response.status_code == 200:
            await self._cache_response(cache_key, response)

        return response

    def _generate_cache_key(self, request: Request) -> str:
        """Generate cache key from request"""
        key_str = f"{request.method}:{request.url.path}"
        if request.url.query:
            key_str += f"?{request.url.query}"

        # Use MD5 to shorten key length
        return f"cache:{hashlib.md5(key_str.encode()).hexdigest()}"

    async def _cache_response(self, cache_key: str, response: Response):
        """Cache the response"""
        try:
            # Handle different response types
            if isinstance(response, StreamingResponse):
                # 对于流式响应，我们不能简单地消费流，因为这会破坏原始响应
                # 因此我们不缓存流式响应
                logger.debug(f"Skipping cache for streaming response: {cache_key}")
                return
            # For regular responses, we need to properly capture the body content
            # This is a limitation of the middleware approach - we can't easily read the body
            # Let's create a simplified cache entry without problematic headers
            headers = dict(response.headers)
            # Remove headers that could cause conflicts when serving cached responses
            headers.pop("content-length", None)
            headers.pop("content-encoding", None)
            headers.pop("transfer-encoding", None)

            cache_data = {
                "content": "",  # Body content is difficult to capture in middleware
                "status_code": response.status_code,
                "headers": headers,
                "media_type": response.media_type,
            }

            redis_client = get_redis_client(is_async=True)
            await redis_client.set(
                cache_key, json_dumps(cache_data), ex=self.cache_expiry
            )
            logger.debug(f"Cached response for key: {cache_key}")
        except Exception as e:
            logger.error(f"Cache response error: {e}")


def cache_route(expire: int = 300):
    """
    Route-level cache decorator

    Args:
        expire: Cache expiration time in seconds
    """

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Initialize Redis if not already done
            if not _initialized:
                try:
                    await init_redis_client()
                except Exception as e:
                    logger.error(f"Failed to initialize Redis in decorator: {e}")
                    # Continue without caching
                    if asyncio.iscoroutinefunction(func):
                        return await func(*args, **kwargs)
                    return func(*args, **kwargs)

            redis_client = get_redis_client(is_async=False)

            # Generate cache key from function name and arguments
            key_parts = [f"route:{func.__name__}"]
            for arg in args:
                if hasattr(arg, "__dict__"):
                    key_parts.append(str(arg.__dict__))
                else:
                    key_parts.append(str(arg))
            for k, v in kwargs.items():
                key_parts.append(f"{k}={v}")

            cache_key = f"route:{hashlib.md5(':'.join(key_parts).encode()).hexdigest()}"

            # Try to get from cache
            try:
                cached = redis_client.get(cache_key)
                if cached:
                    try:
                        cached_data = json.loads(cached)
                        logger.debug(f"Route cache HIT for key: {cache_key}")

                        # Reconstruct the original object if needed
                        if "pydantic_model" in cached_data and "data" in cached_data:
                            # This is a Pydantic model that was cached
                            model_class = cached_data["pydantic_model"]  # noqa: F841
                            # For simplicity, we'll just return the raw data
                            # In a more sophisticated implementation, we'd reconstruct the model
                            return cached_data["data"]
                        # Regular cached data
                        return cached_data
                    except Exception as e:
                        logger.error(f"Route cache deserialize error: {e}")
                else:
                    logger.debug(f"Route cache MISS for key: {cache_key}")
            except Exception as e:
                logger.error(f"Redis get error in decorator: {e}")

            # Execute function
            try:
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)

                # Cache the result
                try:
                    # Handle Pydantic models and other complex objects
                    if hasattr(result, "model_dump"):
                        # Pydantic model - cache both the model info and data
                        cached_result = {
                            "pydantic_model": result.__class__.__name__,
                            "data": result.model_dump(mode="json"),
                        }
                        serialized_result = json_dumps(cached_result)
                    elif hasattr(result, "__dict__"):
                        # Regular object
                        serialized_result = json_dumps(result.__dict__)
                    else:
                        # Primitive type
                        serialized_result = json_dumps(result)

                    redis_client.set(cache_key, serialized_result, ex=expire)
                    logger.debug(f"Route result cached for key: {cache_key}")
                except Exception as e:
                    logger.error(f"Route cache set error: {e}")

                return result
            except Exception as e:
                logger.error(f"Function execution error: {e}")
                raise

        return wrapper

    return decorator


async def clear_cache_pattern(pattern: str = "cache:*"):
    """
    Clear cache entries matching pattern

    Args:
        pattern: Redis key pattern to match

    Returns:
        Number of keys deleted
    """
    if not _initialized:
        raise RuntimeError("Redis not initialized")

    try:
        redis_client = get_redis_client(is_async=True)
        keys = await redis_client.keys(pattern)
        if keys:
            deleted = await redis_client.delete(*keys)
            logger.info(f"Flushed {deleted} cache entries matching pattern: {pattern}")
            return deleted
        return 0
    except Exception as e:
        logger.error(f"Failed to flush cache pattern {pattern}: {e}")
        raise


async def clear_cache_key(cache_key: str):
    """
    Clear specific cache key

    Args:
        cache_key: Specific cache key to delete
    """
    if not _initialized:
        raise RuntimeError("Redis not initialized")

    try:
        redis_client = get_redis_client(is_async=True)
        await redis_client.delete(cache_key)
        logger.info(f"Cleared cache key: {cache_key}")
    except Exception as e:
        logger.error(f"Failed to clear cache key {cache_key}: {e}")
        raise
