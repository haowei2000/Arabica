#!/usr/bin/env python3
"""Monitor WorkspaceContext cache statistics.

Usage:
    # Run once
    python scripts/monitor_workspace_cache.py

    # Continuous monitoring (every 30 seconds)
    python scripts/monitor_workspace_cache.py --watch

    # Custom interval
    python scripts/monitor_workspace_cache.py --watch --interval 60
"""

import argparse
import asyncio
from pathlib import Path
import sys

# Add project root to path
project_root = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(project_root))


def format_bytes(bytes_value: float) -> str:
    """Format bytes to human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f}{unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f}TB"


def print_cache_stats():
    """Print cache statistics."""
    try:
        from structure.utils.workspace_context_cache import get_cache_stats

        stats = get_cache_stats()

        print("=" * 70)
        print("WorkspaceContext Cache Statistics")
        print("=" * 70)
        print(
            f"Cache Size:        {stats['size']:>6} / {stats['maxsize']:<6} workspaces"
        )
        print(f"Usage:             {stats['usage_percent']:>6.2f}%")
        print(f"TTL:               {stats['ttl']:>6} seconds")
        print(
            f"Memory (est):      {format_bytes(stats['estimated_memory_mb'] * 1024 * 1024)}"
        )
        print()

        if stats["size"] > 0:
            print(f"Cached Workspaces ({stats['size']}):")
            for i, ws_id in enumerate(stats["workspaces"][:10], 1):
                print(f"  {i:>2}. {ws_id}")
            if len(stats["workspaces"]) > 10:
                print(f"  ... and {len(stats['workspaces']) - 10} more")
        else:
            print("No workspaces currently cached")

        print("=" * 70)
        print()

    except ImportError as e:
        print(f"Error: Cannot import cache module: {e}")
        print("Make sure you're running from the project root")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


async def watch_cache_stats(interval: int = 30):
    """Continuously monitor cache statistics.

    Args:
        interval: Update interval in seconds
    """
    print(f"Monitoring cache every {interval} seconds (Press Ctrl+C to stop)...")
    print()

    try:
        while True:
            print_cache_stats()
            await asyncio.sleep(interval)
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Monitor WorkspaceContext cache statistics"
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Continuously monitor cache (default: print once)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Update interval in seconds when watching (default: 30)",
    )

    args = parser.parse_args()

    if args.watch:
        asyncio.run(watch_cache_stats(args.interval))
    else:
        print_cache_stats()


if __name__ == "__main__":
    main()
