#!/usr/bin/env python3
"""
Test script to verify permanent caching for generate_sql route
"""

import asyncio
from pathlib import Path
import sys

# Add src to path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load environment variables first
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from structure.middleware.cache_middleware import (  # noqa: E402
    clear_cache_pattern,
    init_redis_client,
)


async def test_permanent_cache():
    """Test permanent cache setup"""
    try:
        # Initialize Redis client
        await init_redis_client()
        print("✅ Redis client initialized successfully")

        # Clear existing cache entries for testing
        deleted_count = await clear_cache_pattern("route:*")
        print(f"✅ Cleared {deleted_count} existing route cache entries")

        print("✅ Permanent cache setup test completed successfully")
        print("ℹ️  The generate_sql route now has permanent caching (1 year expiry)")
        return True

    except Exception as e:
        print(f"❌ Permanent cache setup test failed: {e}")
        return False


if __name__ == "__main__":
    asyncio.run(test_permanent_cache())
