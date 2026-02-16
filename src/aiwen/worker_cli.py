#!/usr/bin/env python3
"""
Agent Worker 启动脚本

功能描述:
    初始化并启动 Agent Worker，包括数据库连接、Redis 客户端和 AgentRegistry 初始化。
    支持运行多个 Worker 实例实现并行处理。

运行方式:
    aiwen-worker                    # 启动单个 worker
    aiwen-worker -n 4               # 启动 4 个 worker
    aiwen-worker --workers 4        # 同上
    aiwen-worker --name my-worker   # 指定 worker 名称前缀

作者: haowei
创建日期: 2025/12/23
最后修改: 2025/12/31
修改人员: haowei
版本: v3.0.0

"""
import asyncio
import logging
import os
import signal
import sys
from typing import Any
import uuid

import click

# 配置会在 aiwen.config.factory 模块导入时自动加载
from aiwen.core.bootstrap import bootstrap_worker
from aiwen.extensions.database import get_session
from aiwen.services.events.event_publisher import REDIS_EXECUTOR_LABEL
from aiwen.services.events.event_worker import Worker

logger = logging.getLogger(__name__)


def generate_consumer_name(prefix: str, index: int) -> str:
    """Generate unique consumer name for a worker."""
    hostname = os.environ.get("HOSTNAME", "local")
    short_uuid = uuid.uuid4().hex[:8]
    return f"{prefix}-{hostname}-{os.getpid()}-{index}-{short_uuid}"


async def run_single_worker(
    redis_client: Any,
    db_factory: Any,
    consumer_name: str,
    worker_index: int,
) -> None:
    """Run a single worker instance."""
    logger.info(f"🚀 Worker [{worker_index}] starting (consumer: {consumer_name})")

    try:
        async with db_factory as session:
            worker = Worker(redis_client, session, consumer_name=consumer_name)
            await worker.start(REDIS_EXECUTOR_LABEL)
    except asyncio.CancelledError:
        logger.info(f"⏹️  Worker [{worker_index}] cancelled")
        raise
    except Exception as e:
        logger.error(f"❌ Worker [{worker_index}] error: {e}", exc_info=True)
        raise


async def run_workers(num_workers: int, name_prefix: str) -> None:
    """Run multiple workers concurrently."""
    logger.info("=" * 60)
    logger.info("🔧 Agent Worker Starting...")
    logger.info(f"   Workers: {num_workers}")
    logger.info(f"   Stream: {REDIS_EXECUTOR_LABEL}")
    logger.info("=" * 60)

    bootstrap = None
    tasks: list[asyncio.Task] = []

    try:
        # Initialize bootstrap
        bootstrap = await bootstrap_worker()
        redis_client = bootstrap.get_redis_client()

        # Create worker tasks
        for i in range(num_workers):
            consumer_name = generate_consumer_name(name_prefix, i)
            db_factory = get_session("aiwen")

            task = asyncio.create_task(
                run_single_worker(redis_client, db_factory, consumer_name, i),
                name=f"worker-{i}",
            )
            tasks.append(task)

        logger.info("=" * 60)
        logger.info(f"🚀 Started {num_workers} worker(s)")
        logger.info("=" * 60)

        # Wait for all workers (or until one fails)
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_EXCEPTION,
        )

        # Check for exceptions
        for task in done:
            if task.exception():
                exception = task.exception()
                logger.error(f"Worker task failed: {exception}")
                # Cancel remaining tasks
                for p in pending:
                    p.cancel()
                if exception is not None:
                    raise exception

    except asyncio.CancelledError:
        logger.info("⚠️  Workers cancelled")
    except KeyboardInterrupt:
        logger.info("\n⚠️  Workers stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Cancel all running tasks
        for task in tasks:
            if not task.done():
                task.cancel()

        # Wait for tasks to complete cancellation
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Cleanup
        if bootstrap:
            logger.info("=" * 60)
            logger.info("🧹 Cleaning up resources...")
            logger.info("=" * 60)
            try:
                await bootstrap.cleanup()
            except Exception as e:
                logger.error(f"⚠️  Error during cleanup: {e}")

        logger.info("=" * 60)
        logger.info("✅ Agent Worker shutdown completed")
        logger.info("=" * 60)


@click.command()
@click.option(
    "-n",
    "--workers",
    default=1,
    show_default=True,
    type=int,
    help="Number of worker instances to run",
)
@click.option(
    "--name",
    default="worker",
    show_default=True,
    type=str,
    help="Worker name prefix for consumer identification",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Enable verbose logging (DEBUG level)",
)
def main(workers: int, name: str, verbose: bool) -> None:
    """
    Start Agent Worker(s) to process tasks from Redis stream.

    Examples:

        # Start single worker
        aiwen-worker

        # Start 4 workers for parallel processing
        aiwen-worker -n 4

        # Start workers with custom name prefix
        aiwen-worker -n 2 --name api-worker
    """
    if verbose:
        logging.getLogger("aiwen").setLevel(logging.DEBUG)

    if workers < 1:
        raise click.BadParameter("Number of workers must be at least 1")

    if workers > 32:
        raise click.BadParameter("Number of workers should not exceed 32")

    # Setup signal handlers
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, initiating shutdown...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run workers
    asyncio.run(run_workers(workers, name))


def main_sync():
    """同步包装函数，用于命令行入口点"""
    main()


if __name__ == "__main__":
    main()
