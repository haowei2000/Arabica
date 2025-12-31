#!/usr/bin/env python3
"""
Test script to verify cache middleware functionality
"""

import asyncio
import sys
from pathlib import Path

# Add src to path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load environment variables first
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / '.env')

from aiwen.middleware.cache_middleware import init_redis_client, clear_cache_pattern

async def test_cache_middleware():
    """Test cache middleware initialization and basic functionality"""
    try:
        # Initialize Redis client
        await init_redis_client()
        print("✅ Redis client initialized successfully")

        # Clear existing cache entries
        deleted_count = await clear_cache_pattern("cache:*")
        print(f"✅ Cleared {deleted_count} existing cache entries")

        print("✅ Cache middleware test completed successfully")
        return True

    except Exception as e:
        print(f"❌ Cache middleware test failed: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(test_cache_middleware())