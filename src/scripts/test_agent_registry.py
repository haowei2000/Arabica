#!/usr/bin/env python3
"""
测试 Agent Registry 是否正确注册

运行方式:
    python src/scripts/test_agent_registry.py
"""

import asyncio
import logging
from pathlib import Path
import sys

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# 加载环境变量 - CRITICAL: Must be before importing any structure modules!
from dotenv import load_dotenv  # noqa: E402

env_file = project_root / "src" / ".env"
print(f"Loading environment from: {env_file}")
if env_file.exists():
    load_dotenv(env_file)
    print("✅ Environment variables loaded")
else:
    print(f"⚠️  Warning: .env file not found at {env_file}")

from structure.registries.core import ExecutorRegistry  # noqa: E402

# 配置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(input)s"
)

logger = logging.getLogger(__name__)


async def test_registry():
    """测试 AgentRegistry"""
    logger.info("=" * 60)
    logger.info("Testing Agent Registry")
    logger.info("=" * 60)

    # 1. 初始化前检查
    logger.info("\n1. Before initialization:")
    templates = ExecutorRegistry.list()
    logger.info(f"   Registered templates: {templates}")
    logger.info(f"   Count: {len(templates)}")

    # 2. 初始化 AgentRegistry (新系统自动发现模块)
    logger.info("\n2. Initializing Agent Registry...")
    try:
        # In the new unified registry system, discovery happens via bootstrap
        # For testing purposes, we manually trigger executor discovery
        ExecutorRegistry.discover_and_import_executors()
        logger.info("   ✓ Initialization complete")
    except Exception as e:
        logger.error(f"   ✗ Initialization failed: {e}", exc_info=True)
        return False

    # 3. 初始化后检查
    logger.info("\n3. After initialization:")
    templates = ExecutorRegistry.list()
    logger.info(f"   Registered templates: {templates}")
    logger.info(f"   Count: {len(templates)}")

    # 4. 检查 DEFAULT001
    logger.info("\n4. Checking DEFAULT001:")
    if ExecutorRegistry.is_registered("DEFAULT001"):
        logger.info("   ✓ DEFAULT001 is registered")
        try:
            agent_cls = ExecutorRegistry.get("DEFAULT001")
            logger.info(f"   ✓ Agent class: {agent_cls}")
            logger.info(f"   ✓ Class name: {agent_cls.__name__}")
        except Exception as e:
            logger.error(f"   ✗ Failed to get DEFAULT001: {e}")
            return False
    else:
        logger.error("   ✗ DEFAULT001 is NOT registered")
        return False

    # 5. 测试创建实例
    logger.info("\n5. Testing instance creation:")
    try:
        agent_cls = ExecutorRegistry.get("DEFAULT001")
        config = {"model_provider": "openai", "model_name": "gpt-4.1-mini"}
        agent = agent_cls(config)
        logger.info(f"   ✓ Instance created: {agent}")
        logger.info(f"   ✓ Instance type: {type(agent)}")
    except Exception as e:
        logger.error(f"   ✗ Failed to create instance: {e}", exc_info=True)
        return False

    logger.info("\n" + "=" * 60)
    logger.info("✓ All tests passed!")
    logger.info("=" * 60)
    return True


async def main():
    """主函数"""
    try:
        success = await test_registry()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
