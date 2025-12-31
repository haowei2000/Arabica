# Redis Cache Integration for FastAPI

This document explains how to use the Redis cache functionality that has been integrated into the FastAPI application.

## Overview

The Redis cache integration provides:
1. Automatic Redis connection management
2. A caching decorator for FastAPI routes
3. Health check endpoints
4. Cache flushing capabilities

## Setup

1. Ensure Redis is installed and running on your system
2. Configure Redis connection settings in your `.env` file:
   ```
   REDIS__HOST=localhost
   REDIS__PORT=6379
   REDIS__DB=0
   REDIS__PASSWORD=
   ```

3. Install the required dependencies:
   ```bash
   pip install -r requirements-redis.txt
   ```

## Usage

### 1. Caching Decorator

Use the `@cache_ttl(seconds)` decorator to cache the response of any FastAPI route:

```python
from aiwen.extensions.redis import cache_ttl

@app.get("/expensive-operation")
@cache_ttl(300)  # Cache for 5 minutes
async def expensive_operation():
    # This operation will be cached for 300 seconds
    result = perform_expensive_computation()
    return result
```

### 2. Test Endpoint

A test endpoint has been added to demonstrate the caching functionality:

```
GET /api/nl2sql/test-cache
```

The first request will take ~2 seconds, subsequent requests within 30 seconds will be served from cache.

### 3. Cache Flushing

To manually flush the cache:

```
DELETE /api/nl2sql/flush-cache
```

### 4. Health Check

The main health check endpoint now includes Redis status:

```
GET /health
```

## How It Works

1. During application startup, a Redis connection is established
2. The `@cache_ttl` decorator generates a unique cache key based on the request
3. Responses are serialized using pickle and stored in Redis with TTL
4. On subsequent requests, cached responses are served directly from Redis
5. During shutdown, the Redis connection is properly closed

## Customization

You can customize the cache behavior by modifying the `src/aiwen/extensions/redis.py` file:

- Change serialization method (currently using pickle)
- Modify cache key generation strategy
- Adjust default TTL values
- Add cache warming strategies

## Error Handling

If Redis is not available or encounters an error:
- The application will continue to function normally
- Routes with cache decorators will bypass caching
- Errors are logged but don't affect the main application flow