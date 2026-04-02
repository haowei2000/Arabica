#!/usr/bin/env python3
"""
Test script to verify Redis cache functionality for SQL generation
"""

import asyncio
from pathlib import Path
import sys

# Add src to path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load environment variables first
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from structure.schemas.nl2sql.generate_sql import SqlResponse  # noqa: E402
from structure.schemas.nl2sql.indicator_info import (  # noqa: E402
    DimensionInfoSchema,
    IndicatorInfoSchema,
    TableSchema,
)
from structure.services.nl2sql.generate_sql import generate_sql  # noqa: E402


async def test_sql_cache():
    """Test SQL generation with caching"""
    # Create test data
    indicator_info = IndicatorInfoSchema(
        name="test_indicator",
        sql_template="SELECT 1 as test",
        dimensions=[
            DimensionInfoSchema(name="test_dim", code="TD", alias="test_alias")
        ],
        related_tables=[TableSchema(table_name="test_table", table_description=None)],
    )

    print("🔍 Testing SQL generation with cache...")

    # First call - should generate SQL and cache it
    print("\n📝 First call (should generate SQL):")
    try:
        result1 = await generate_sql(query="测试查询", indicator_info=indicator_info)
        print(f"✅ First call successful: {result1}")

        # Store the SQL string for comparison
        sql1 = (
            result1.sql
            if hasattr(result1, "sql")
            else result1.get("sql", "")
            if isinstance(result1, dict)
            else str(result1)
        )
    except Exception as e:
        print(f"❌ First call failed: {e}")
        return False

    # Second call - should return cached result
    print("\n📝 Second call (should use cache):")
    try:
        result2 = await generate_sql(query="测试查询", indicator_info=indicator_info)
        print(f"✅ Second call successful: {result2}")

        # Check if results are the same
        sql2 = (
            result2.sql
            if hasattr(result2, "sql")
            else result2.get("sql", "")
            if isinstance(result2, dict)
            else str(result2)
        )

        if sql1 == sql2:
            print("✅ Cache working correctly - same results returned")
        else:
            print("⚠️  Results differ - cache may not be working")
            print(f"  First result SQL: {sql1}")
            print(f"  Second result SQL: {sql2}")

    except Exception as e:
        print(f"❌ Second call failed: {e}")
        return False

    return True


if __name__ == "__main__":
    asyncio.run(test_sql_cache())
