#!/usr/bin/env python3
"""
Agent Worker 启动脚本

功能描述:
    初始化并启动 Agent Worker，包括数据库连接、Redis 客户端和 AgentRegistry 初始化。
    使用统一的 AppSettings 管理配置，不再直接加载环境变量。

运行方式:
    aiwen-worker
    或
    python -m aiwen.worker_cli

作者: haowei
创建日期: 2025/12/23
最后修改: 2025/12/31
修改人员: haowei
版本: v2.0.0

"""

import asyncio
import logging
import signal
import sys

# 配置会在 aiwen.config.factory 模块导入时自动加载
# 不需要在这里调用 load_dotenv()
from aiwen.core.bootstrap import bootstrap_worker
from aiwen.extensions.database import get_session
from aiwen.workers.event_worker import AGENT_WORKER_STREAM, Worker

logger = logging.getLogger(__name__)


async def start_worker(redis_client, db_session):
    """Instantiate the Worker and start polling the Redis stream."""
    worker = Worker(redis_client, db_session)
    await worker.start(AGENT_WORKER_STREAM)


async def run():
    """主函数 - 初始化并启动 Agent Worker"""
    logger.info("=" * 60)
    logger.info("🔧 Agent Worker Starting...")
    logger.info("=" * 60)
    bootstrap = None

    try:
        # ==================== 使用统一的初始化流程 ====================
        # Bootstrap 内部会调用 get_settings() 获取配置
        bootstrap = await bootstrap_worker()

        # 获取Redis客户端和数据库工厂
        redis_client = bootstrap.get_redis_client()
        db = get_session("aiwen")

        # 设置信号处理器
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # ==================== 启动 Worker ====================
        logger.info("=" * 60)
        logger.info("🚀 Starting Worker Loop...")
        logger.info("=" * 60)
        async with db as session:
            await start_worker(redis_client, session)

    except KeyboardInterrupt:
        logger.info("\n⚠️  Worker stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error(f"❌ Fatal error in worker: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # 使用统一的清理流程
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


def main_sync():
    """同步包装函数，用于命令行入口点"""
    asyncio.run(run())


if __name__ == "__main__":
    main_sync()
