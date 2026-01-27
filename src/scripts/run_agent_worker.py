#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent Worker 启动脚本

运行方式:
    python src/scripts/run_agent_worker.py
"""

import asyncio
import logging
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# 加载环境变量 - CRITICAL: Must be before importing any aiwen modules!
from dotenv import load_dotenv

env_file = project_root / "src" / ".env"
print(f"🔧 Loading environment from: {env_file}")
if env_file.exists():
    load_dotenv(env_file)
    print("✅ Environment variables loaded")
else:
    print(f"⚠️  Warning: .env file not found at {env_file}")
    print("   Worker may fail if environment variables are not set!")

from aiwen.workers.task_worker import start_worker
from aiwen.extensions.database import get_session
from aiwen.middleware.cache_middleware import get_redis_client, init_redis_client
from aiwen.services.agents.agent_registry import init_agent_registry

# 确保日志目录存在
log_dir = project_root / "logs"
log_dir.mkdir(exist_ok=True)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(input)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_dir / "agent_worker.log"),
    ],
)

logger = logging.getLogger(__name__)


async def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("Agent Worker Starting...")
    logger.info("=" * 60)

    try:
        # 初始化 Redis 客户端
        await init_redis_client()
        redis_client = get_redis_client(is_async=True)

        if not redis_client:
            raise RuntimeError("Failed to initialize Redis client")

        logger.info("Redis client initialized successfully")

        # 初始化 Agent Registry
        print("=" * 60)
        print("🔧 Initializing Agent Registry...")
        print("=" * 60)
        logger.info("Initializing Agent Registry...")

        try:
            await init_agent_registry()

            # 验证注册是否成功
            from aiwen.services.agents.agent_registry import AgentRegistry

            registered_templates = AgentRegistry.list()

            print(f"✅ Agent Registry initialized successfully!")
            print(f"📋 Registered templates: {registered_templates}")
            logger.info(
                f"Agent Registry initialized with templates: {registered_templates}"
            )

            # 特别检查 DEFAULT001
            if AgentRegistry.is_registered("DEFAULT001"):
                print("✅ DEFAULT001 is registered and ready")
                logger.info("DEFAULT001 template verified")
            else:
                print("❌ WARNING: DEFAULT001 is NOT registered!")
                logger.error("DEFAULT001 template not found after initialization")
                raise RuntimeError("DEFAULT001 template registration failed")

        except Exception as e:
            print(f"❌ Failed to initialize Agent Registry: {e}")
            logger.error(f"Agent Registry initialization failed: {e}", exc_info=True)
            raise

        # 获取数据库会话工厂 - 明确指定使用 aiwen 数据库
        from functools import partial

        db_factory = partial(get_session, db_name="aiwen")

        # 启动 worker
        logger.info("Starting worker loop...")
        await start_worker(redis_client, db_factory)

    except KeyboardInterrupt:
        logger.info("\nWorker stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error(f"Fatal error in worker: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Agent Worker shutdown completed")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete")
