# Redis Cache Middleware for FastAPI

This document explains the middleware-based Redis cache implementation for the FastAPI application.

## Overview

The Redis cache middleware provides automatic caching for GET requests using Redis. Unlike the previous decorator-based approach, this middleware approach:

1. Automatically caches all GET requests (except excluded paths)
2. Requires no code changes to individual endpoints
3. Provides centralized cache management
4. Follows the same pattern as the example provided

## Implementation Details

### 1. Cache Middleware (`src/aiwen/middleware/cache_middleware.py`)

Key features:
- **Automatic Caching**: Caches all GET requests with status code 200
- **Path Exclusion**: Excludes certain paths like `/docs`, `/openapi.json`, `/redoc`, `/health`
- **Cache Keys**: Generates cache keys using MD5 hash of request method, path, and query parameters
- **TTL Management**: Configurable cache expiration time (default 300 seconds)
- **Route-level Decorator**: Additional `@cache_route()` decorator for fine-grained control

### 2. Application Integration (`src/aiwen/app.py`)

- **Initialization**: Redis clients initialized during app startup
- **Middleware Registration**: Cache middleware added to the app
- **Cleanup**: Redis clients properly closed during app shutdown
- **Health Check**: Updated to reflect Redis middleware status

### 3. Route Integration (`src/aiwen/routers/nl2sql.py`)

- **Test Endpoint**: Added `/api/nl2sql/test-cache` to demonstrate caching
- **Cache Flush Endpoint**: Added `/api/nl2sql/flush-cache` to clear cache entries

## How It Works

1. **Request Flow**:
   - Incoming GET request reaches the middleware
   - Middleware generates a cache key based on request details
   - Checks Redis for cached response
   - If found, returns cached response immediately
   - If not found, passes request to next middleware/handler

2. **Response Caching**:
   - After handler processes request, middleware receives response
   - If status code is 200, serializes and stores response in Redis
   - Sets TTL for automatic expiration

3. **Cache Key Generation**:
   - Format: `cache:{MD5_HASH}`
   - Hash based on: `METHOD:PATH?QUERY_PARAMS`
   - Example: `GET:/api/users?id=123` → `cache:abcd1234...`

## Usage

### 1. Automatic Caching

All GET requests are automatically cached:
```
GET /api/some-endpoint          # First call - processed normally
GET /api/some-endpoint          # Second call - served from cache
GET /api/some-endpoint?param=1  # Different query - separate cache entry
```

### 2. Manual Cache Control

Clear cache entries:
```
DELETE /api/nl2sql/flush-cache?pattern=cache:*
```

### 3. Route-level Caching

For more control, use the `@cache_route()` decorator:
```python
from aiwen.middleware.cache_middleware import cache_route

@app.get("/custom-cache")
@cache_route(expire=600)  # Cache for 10 minutes
async def custom_cached_endpoint():
    return {"data": "custom cached response"}
```

## Configuration

The middleware uses the existing Redis configuration from your settings:
```
REDIS__HOST=localhost
REDIS__PORT=6379
REDIS__DB=0
REDIS__PASSWORD=
```

## Excluded Paths

By default, these paths are not cached:
- `/docs`
- `/openapi.json`
- `/redoc`
- `/health`

You can modify this list when initializing the middleware.

## Benefits Over Previous Implementation

1. **No Code Changes Required**: Existing endpoints automatically benefit from caching
2. **Centralized Management**: All cache logic in one place
3. **Better Performance**: No decorator overhead on each function call
4. **More Flexible**: Can easily exclude/include paths
5. **Consistent Approach**: Follows the middleware pattern used elsewhere in FastAPI

## Testing

Test the cache with the provided endpoint:
```
GET /api/nl2sql/test-cache
```

The first request will take ~1 second, subsequent requests within 5 minutes will be instant.