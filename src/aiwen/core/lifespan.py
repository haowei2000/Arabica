"""Application lifespan management for FastAPI."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from aiwen.core.bootstrap import bootstrap_api

logger = logging.getLogger(__name__)


async def create_admin_user():
    """Create a default admin user if one doesn't already exist."""
    try:
        async with get_session("aiwen") as session:
            # Check if default tenant exists, create if not
            result = await session.execute(
                select(Tenant).where(Tenant.name == "default")
            )
            tenant = result.scalar_one_or_none()

            if not tenant:
                logger.info("Creating default tenant...")
                tenant = Tenant(name="default", description="Default tenant")
                session.add(tenant)
                await session.commit()
                await session.refresh(tenant)
                logger.info("Default tenant created")
            else:
                logger.info("Default tenant already exists")

            settings = get_settings()
            admin_password = settings.auth.admin_password
            admin_username = settings.auth.admin_username
            admin_email = settings.auth.admin_email
            # Check if admin user already exists
            result = await session.execute(
                select(User).where(User.username == admin_username)
            )
            admin_user = result.scalar_one_or_none()

            if admin_user:
                logger.info("Admin user already exists")
                return

            # Create admin user
            logger.info("Creating admin user...")

            # Hash the password

            hashed_password = hash_password(admin_password)

            # Create the admin user
            admin_user = User(
                username=admin_username,
                email=admin_email,
                password_hash=hashed_password,
                tenant_id=tenant.id,
                role="admin",
                is_superuser=True,
                is_active=True,
            )

            session.add(admin_user)
            await session.flush()  # 确保获取到新创建用户的ID

            # Update tenant's admin_id
            tenant.admin_id = admin_user.id
            session.add(tenant)

            await session.commit()
            await session.refresh(admin_user)

            logger.info("Admin user created successfully!")
            logger.info("   Username: admin")
            logger.info(f"   Password: {admin_password}")
            logger.info(f"   Email: {admin_user.email}")
            logger.info(f"   Role: {admin_user.role}")
    except Exception as e:
        logger.error(f"Error creating admin user: {e}")


async def register_all_dimensions():
    import importlib

    import aiwen.services.nl2sql.dimension_registry.dimensions

    importlib.reload(aiwen.services.nl2sql.dimension_registry.dimensions)


async def initialize_redis() -> None:
    """Initialize Redis client connections."""
    try:
        from aiwen.middleware.cache_middleware import init_redis_client

        await init_redis_client()
        logger.info("Redis clients initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize Redis clients: %s", e)


async def initialize_databases() -> None:
    """
    初始化数据库连接（已废弃，保留用于向后兼容）

    ⚠️  此函数已被 bootstrap.py 中的统一初始化流程替代。
    ⚠️  表的创建应该由 Alembic 管理，不在代码中自动创建。

    新的初始化方式请参考：
    - aiwen.core.bootstrap.ApplicationBootstrap
    """
    # 触发数据库注册（懒加载）
    _ensure_registered()

    # ⚠️  不再自动创建表 - 表应该由 Alembic 管理
    logger.warning("⚠️  此函数已废弃，请使用 bootstrap.py 中的统一初始化流程")
    logger.warning("   表的创建请使用: alembic upgrade head")


async def initialize_agent_registry() -> None:
    """Initialize agent registry with default templates."""
    try:
        from aiwen.services.agents.agent_registry import init_agent_registry

        await init_agent_registry()
        logger.info("Agent registry initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize agent registry: %s", e)
        # Don't raise - allow app to start even if agent registry fails


async def shutdown_redis() -> None:
    """Close Redis client connections."""
    try:
        from aiwen.middleware.cache_middleware import close_redis_client

        await close_redis_client()
        logger.info("Redis clients closed successfully")
    except Exception as e:
        logger.error("Error closing Redis clients: %s", e)


async def shutdown_databases() -> None:
    """Close all database engine connections."""
    logger.info("Closing database connections...")
    for bind_name, engine in _engines.items():
        await engine.dispose()
        logger.info("Database %s disposed", bind_name)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Manage application lifespan: startup and shutdown events.

    使用统一的Bootstrap模块进行初始化和清理。
    """
    logger.info("=== FastAPI Application starting up ===")

    # 使用统一的初始化流程
    bootstrap = await bootstrap_api()

    logger.info("=== FastAPI Application startup complete ===")

    yield

    # 使用统一的清理流程
    logger.info("=== FastAPI Application shutting down ===")
    await bootstrap.cleanup()
    logger.info("=== FastAPI Application shutdown complete ===")
