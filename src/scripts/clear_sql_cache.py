#!/usr/bin/env python3
"""
Script to clear SQL generation cache in Redis
"""

import asyncio
import logging
from pathlib import Path
import sys

# Add src to path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load environment variables first
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from aiwen.middleware.cache_middleware import clear_cache_pattern, init_redis_client

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def clear_sql_cache():
    """Clear SQL generation cache from Redis"""
    try:
        # Initialize Redis client
        await init_redis_client()
        logger.info("Redis client initialized successfully")

        # Clear cache entries related to SQL generation
        # This will clear all cache entries, you can be more specific if needed
        deleted_count = await clear_cache_pattern("cache:*")
        logger.info(f"Successfully cleared {deleted_count} cache entries")

        # You can also clear route-level cache if needed
        deleted_count_routes = await clear_cache_pattern("route:*")
        logger.info(f"Successfully cleared {deleted_count_routes} route cache entries")

        print("✅ Cache cleared successfully!")
        return True

    except Exception as e:
        logger.error(f"Failed to clear cache: {e}")
        print(f"❌ Failed to clear cache: {e}")
        return False


if __name__ == "__main__":
    asyncio.run(clear_sql_cache())
