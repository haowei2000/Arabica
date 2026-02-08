#!/usr/bin/env python3
"""
统一的应用初始化模块

提供标准化的初始化流程，确保所有服务（API、Worker、MCP）使用一致的配置和初始化逻辑。

作者: haowei
创建日期: 2025/12/31
版本: v1.0.0
"""

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from aiwen.config.factory import get_settings
from aiwen.extensions.database import _engines, _ensure_registered, get_session
from aiwen.extensions.logger import setup_logging
from aiwen.extensions.storage.global_storage import get_global_s3_storage
from aiwen.models.auth.tenant import Tenant
from aiwen.models.auth.user import User
from aiwen.utils.security import hash_password

logger = logging.getLogger(__name__)


@dataclass
class BootstrapConfig:
    """
    初始化配置

    控制哪些组件需要初始化，不同服务可以有不同的需求：
    - API: 需要全部初始化
    - Worker: 需要 Redis + Agent Registry，不需要创建表（API已创建）
    - MCP: 需要数据库，其他可选
    """

    # 是否初始化日志
    init_logging: bool = True

    # 是否初始化Redis
    init_redis: bool = True

    # 是否初始化数据库连接
    init_database: bool = True

    # 是否创建数据库表（只有API服务需要）
    create_tables: bool = False

    # 是否创建管理员用户（只有API服务需要）
    create_admin_user: bool = False

    # 是否初始化 Agent Registry
    init_agent_registry: bool = True

    init_storage: bool = True


async def _initialize_databases() -> None:
    """初始化数据库连接"""
    logger.info("🗄️  初始化数据库连接...")
    _ensure_registered()
    logger.info(f"✅ 已注册 {len(_engines)} 个数据库")


async def _create_tables() -> None:
    """
    创建数据库表（已禁用）

    ⚠️  表的创建应该统一由 Alembic 管理，不应该在代码中自动创建。

    使用 Alembic 创建表的步骤：
    1. 运行: alembic upgrade head
    2. 如果需要新表，先创建模型，然后: alembic revision --autogenerate -m "add new table"
    3. 再运行: alembic upgrade head

    如果确实需要在代码中创建表（仅用于测试环境），请手动调用 Base.metadata.create_all()
    """
    logger.warning("⚠️  跳过数据库表创建 - 表应该由 Alembic 管理")
    logger.warning("   请确保已运行: alembic upgrade head")
    # 不再自动创建表
    # 如果需要创建表，请使用 Alembic:
    # $ alembic upgrade head


def _setup_logging() -> None:
    """设置日志系统"""
    logger.info("📝 设置日志系统...")
    setup_logging()
    logger.info("✅ 日志系统已配置")


async def _shutdown_redis() -> None:
    """关闭Redis连接"""
    logger.info("🔴 关闭Redis连接...")
    try:
        from aiwen.middleware.cache_middleware import close_redis_client

        await close_redis_client()
        logger.info("✅ Redis连接已关闭")
    except Exception as e:
        logger.error(f"⚠️  关闭Redis连接时出错: {e}")


async def _shutdown_databases() -> None:
    """关闭所有数据库连接"""
    logger.info("🗄️  关闭数据库连接...")
    for bind_name, engine in _engines.items():
        try:
            await engine.dispose()
            logger.info(f"   ✅ 数据库 {bind_name} 已关闭")
        except Exception as e:
            logger.error(f"   ⚠️  关闭数据库 {bind_name} 时出错: {e}")
    logger.info("✅ 所有数据库连接已关闭")


async def _initialize_agent_registry() -> None:
    """初始化Agent Registry"""
    logger.info("🤖 初始化Agent Registry...")
    try:
        from aiwen.services.executor.executor_registry import (
            init_executor_registry,
            ExecutorRegistry,
        )

        await init_executor_registry()

        templates = ExecutorRegistry.list()
        logger.info(f"✅ Agent Registry初始化完成")
        logger.info(f"   已注册模板: {templates}")

        # 验证关键模板
        if ExecutorRegistry.is_registered("DEFAULT001"):
            logger.info("   ✅ DEFAULT001模板验证通过")
        else:
            logger.warning("   ⚠️  DEFAULT001模板未注册")
    except Exception as e:
        logger.error(f"❌ Agent Registry初始化失败: {e}")
        # 不抛出异常，允许应用继续运行


class ApplicationBootstrap:
    """应用初始化管理器"""

    def __init__(self, config: Optional[BootstrapConfig] = None):
        """
        初始化 Bootstrap

        Args:
            config: 初始化配置，如果为None则使用默认配置
        """
        self.config = config or BootstrapConfig()
        self.settings = get_settings()
        self.redis_client = None
        self.storage = None

    async def initialize(self) -> None:
        """
        执行完整的初始化流程

        按照正确的顺序初始化各个组件
        """
        logger.info("=" * 60)
        logger.info("🚀 应用初始化开始")
        logger.info("=" * 60)

        # Step 1: 日志系统（如果需要）
        if self.config.init_logging:
            _setup_logging()

        # Step 2: 数据库连接
        if self.config.init_database:
            await _initialize_databases()

        # Step 3: 数据库表创建（仅API服务）
        if self.config.create_tables:
            await _create_tables()

        # Step 4: Redis连接
        if self.config.init_redis:
            await self._initialize_redis()

        # Step 5: 创建管理员用户（仅API服务）
        if self.config.create_admin_user:
            await self._create_admin_user()

        # Step 6: Agent Registry初始化
        if self.config.init_agent_registry:
            await _initialize_agent_registry()

        if self.config.init_storage:
            await self._init_storage_backend()
        logger.info("=" * 60)
        logger.info("✅ 应用初始化完成")
        logger.info("=" * 60)

    async def cleanup(self) -> None:
        """清理所有资源"""
        logger.info("=" * 60)
        logger.info("🧹 清理资源开始")
        logger.info("=" * 60)

        # 关闭Redis连接
        if self.config.init_redis:
            await _shutdown_redis()

        # 关闭数据库连接
        if self.config.init_database:
            await _shutdown_databases()

        logger.info("=" * 60)
        logger.info("✅ 资源清理完成")
        logger.info("=" * 60)

    # ========================================================================
    # 私有方法 - 各个初始化步骤
    # ========================================================================

    async def _initialize_redis(self) -> None:
        """初始化Redis连接"""
        logger.info("🔴 初始化Redis连接...")
        try:
            from aiwen.middleware.cache_middleware import (
                init_redis_client,
                get_redis_client,
            )

            await init_redis_client()
            self.redis_client = get_redis_client(is_async=True)

            if not self.redis_client:
                raise RuntimeError("Redis client initialization returned None")

            logger.info(
                f"✅ Redis连接成功: {self.settings.redis.host}:{self.settings.redis.port}"
            )
        except Exception as e:
            logger.error(f"❌ Redis初始化失败: {e}")
            raise

    async def _init_storage_backend(self) -> None:
        """获取存储后端（供Worker使用）"""
        from aiwen.extensions.storage.global_storage import init_global_s3_storage
        init_global_s3_storage(
            endpoint_url=self.settings.rustfs.endpoint,
            bucket=self.settings.rustfs.bucket,
            access_key=self.settings.rustfs.access_key,
            secret_key=self.settings.rustfs.secret_key,
            use_ssl=self.settings.rustfs.secure
        )
        self.storage = get_global_s3_storage()
        logger.info("Storage Init Successfully")

    async def _create_admin_user(self) -> None:
        """创建管理员用户"""
        logger.info("👤 检查管理员用户...")
        try:
            async with get_session("aiwen") as session:
                # 检查默认租户
                result = await session.execute(
                    select(Tenant).where(Tenant.name == "default")
                )
                tenant = result.scalar_one_or_none()

                if not tenant:
                    logger.info("   创建默认租户...")
                    tenant = Tenant(name="default", description="Default tenant")
                    session.add(tenant)
                    await session.commit()
                    await session.refresh(tenant)
                    logger.info("   ✅ 默认租户已创建")
                else:
                    logger.info("   ✅ 默认租户已存在")

                # 检查管理员用户
                admin_username = self.settings.auth.admin_username
                result = await session.execute(
                    select(User).where(User.username == admin_username)
                )
                admin_user = result.scalar_one_or_none()

                if admin_user:
                    logger.info(f"   ✅ 管理员用户已存在: {admin_username}")
                    return

                # 创建管理员用户
                logger.info(f"   创建管理员用户: {admin_username}")
                hashed_password = hash_password(self.settings.auth.admin_password)

                admin_user = User(
                    username=admin_username,
                    email=self.settings.auth.admin_email,
                    password_hash=hashed_password,
                    tenant_id=tenant.id,
                    role="admin",
                    is_superuser=True,
                    is_active=True,
                )

                session.add(admin_user)
                await session.flush()

                # 更新租户的管理员ID
                tenant.admin_id = admin_user.id
                session.add(tenant)

                await session.commit()
                await session.refresh(admin_user)

                logger.info(f"   ✅ 管理员用户已创建")
                logger.info(f"      用户名: {admin_username}")
                logger.info(f"      邮箱: {admin_user.email}")
                logger.info(f"      角色: {admin_user.role}")
        except Exception as e:
            logger.error(f"❌ 创建管理员用户失败: {e}")
            # 不抛出异常，允许应用继续运行

    def get_redis_client(self):
        """获取Redis客户端（供Worker使用）"""
        return self.redis_client


# ============================================================================
# 预定义的配置模板
# ============================================================================


def get_api_bootstrap_config() -> BootstrapConfig:
    """
    API服务的初始化配置

    注意：
    - create_tables=False: 表由 Alembic 统一管理，不在代码中创建
    - create_admin_user=True: API 负责创建管理员用户
    """
    return BootstrapConfig(
        init_logging=True,
        init_redis=True,
        init_database=True,
        create_tables=False,  # ⚠️  表由 Alembic 管理，不在代码中创建
        create_admin_user=True,  # API负责创建用户
        init_agent_registry=True,
        init_storage=True,
    )


def get_worker_bootstrap_config() -> BootstrapConfig:
    """
    Worker服务的初始化配置

    注意：
    - create_tables=False: 表由 Alembic 统一管理
    - create_admin_user=False: Worker 不需要创建用户
    """
    return BootstrapConfig(
        init_logging=True,
        init_redis=True,
        init_database=True,
        create_tables=False,  # 表由 Alembic 管理
        create_admin_user=False,  # Worker不创建用户
        init_agent_registry=True,  # Worker需要Agent Registry
        init_storage=True
    )


def get_mcp_bootstrap_config() -> BootstrapConfig:
    """
    MCP服务的初始化配置 - 最小化初始化

    注意：
    - create_tables=False: 表由 Alembic 统一管理
    - init_redis=False: MCP 不依赖 Redis
    """
    return BootstrapConfig(
        init_logging=True,
        init_redis=False,  # MCP不需要Redis
        init_database=True,
        create_tables=False,  # 表由 Alembic 管理
        create_admin_user=False,  # MCP不创建用户
        init_agent_registry=False,  # MCP不需要Agent Registry
        init_storage=False
    )


def get_alembic_bootstrap_config() -> BootstrapConfig:
    """
    Alembic的初始化配置

    注意：
    - create_tables=False: 表由 Alembic 统一管理
    - create_admin_user=False: Alembic 不需要创建用户
    """
    return BootstrapConfig(
        init_logging=False,
        init_redis=False,
        init_database=True,
        create_tables=False,
        create_admin_user=False,
        init_agent_registry=False,
        init_storage=False
    )


def get_celery_bootstrap_config() -> BootstrapConfig:
    """
    Celery的初始化配置

    """
    return BootstrapConfig(
        init_logging=False,
        init_redis=False,
        init_database=True,
        create_tables=False,
        create_admin_user=False,
        init_agent_registry=False,
        init_storage=True
    )


async def bootstrap_alembic() -> ApplicationBootstrap:
    """初始化Alembic服务"""
    bootstrap = ApplicationBootstrap(get_alembic_bootstrap_config())
    await bootstrap.initialize()
    return bootstrap


async def bootstrap_api() -> ApplicationBootstrap:
    """初始化API服务"""
    bootstrap = ApplicationBootstrap(get_api_bootstrap_config())
    await bootstrap.initialize()
    return bootstrap


async def bootstrap_worker() -> ApplicationBootstrap:
    """初始化Worker服务"""
    bootstrap = ApplicationBootstrap(get_worker_bootstrap_config())
    await bootstrap.initialize()
    return bootstrap


async def bootstrap_mcp() -> ApplicationBootstrap:
    """初始化MCP服务"""
    bootstrap = ApplicationBootstrap(get_mcp_bootstrap_config())
    await bootstrap.initialize()
    return bootstrap


async def bootstrap_celery() -> ApplicationBootstrap:
    """初始化Celery服务"""
    bootstrap = ApplicationBootstrap(get_celery_bootstrap_config())
    await bootstrap.initialize()
    return bootstrap
