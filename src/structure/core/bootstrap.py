#!/usr/bin/env python3
"""
Unified Application Bootstrap Module (v2 - with Centralized Registry System)

Provides standardized initialization flow with the new centralized registry system.
This version simplifies registry initialization by using the unified registry manager.

Changes from v1:
- Replaced separate _initialize_agent_registry() and _sync_inner_tools()
- with single _initialize_registries() using RegistryManager
- Cleaner imports from structure.registries
- Reduced code duplication

Author: haowei
Created: 2025/12/31
Version: v2.0.0
"""

from dataclasses import dataclass
import logging

from sqlalchemy import select

from structure.extensions.database import _engines, _ensure_registered, get_session
from structure.extensions.logger import setup_logging
from structure.extensions.storage.global_storage import get_global_s3_storage
from structure.models.auth.tenant import Tenant
from structure.models.auth.user import User
from structure.services.context.init_sys_context import ensure_default_context_paths
from structure.utils.security import hash_password

logger = logging.getLogger(__name__)


@dataclass
class BootstrapConfig:
    """
    Initialization Configuration

    Controls which components need initialization:
    - API: Needs everything
    - Worker: Needs Redis + Registry, no table creation
    - MCP: Needs database only
    """

    # Whether to initialize logging
    init_logging: bool = True

    # Whether to initialize Redis
    init_redis: bool = True

    # Whether to initialize a database connection
    init_database: bool = True

    # Whether to create database tables (only API service needs this)
    create_tables: bool = False

    # Whether to create an admin user (only API service needs this)
    create_admin_user: bool = False

    # Whether to initialize registries (unified: tools, executors, etc.)
    init_registries: bool = True

    # Whether to initialize storage
    init_storage: bool = True

    # Whether to seed default context paths in all workspaces (API only)
    init_context_paths: bool = False

    # Whether to discover inner tools from the filesystem (Plugin discovery)
    discover_inner_tools: bool = True


async def _initialize_databases() -> None:
    """Initialize database connections"""
    logger.info("Initializing database connections...")
    _ensure_registered()
    logger.info(f"Registered {len(_engines)} databases")


async def _create_tables() -> None:
    """
    Create database tables (disabled)

    Tables should be managed by Alembic, not auto-created in code.

    To create tables:
    1. Run: alembic upgrade head
    2. For new tables, create model first: alembic revision --autogenerate -m "add new table"
    3. Then run: alembic upgrade head
    """
    logger.warning("Skipping database table creation - tables managed by Alembic")
    logger.warning("   Please ensure you've run: alembic upgrade head")


def _setup_logging() -> None:
    """Setup logging system"""
    logger.info("Setting up logging system...")
    setup_logging()
    logger.info("Logging system configured")


async def _shutdown_redis() -> None:
    """Shutdown Redis connection"""
    logger.info("Shutting down Redis connection...")
    try:
        from structure.middleware.cache_middleware import close_redis_client

        await close_redis_client()
        logger.info("Redis connection closed")
    except Exception as e:
        logger.error(f"Error closing Redis connection: {e}")


async def _shutdown_databases() -> None:
    """Shutdown all database connections"""
    logger.info("🗄️  Shutting down database connections...")
    for bind_name, engine in _engines.items():
        try:
            await engine.dispose()
            logger.info(f"Database {bind_name = } closed")
        except Exception as e:
            logger.error(f"Error closing database {bind_name = }: {e}")
    logger.info("All database connections closed")


async def _initialize_registries(config: BootstrapConfig) -> None:
    """
    Initialize all registries using a centralized system.

    This replaces the old separate initialization:
    - _initialize_agent_registry() (executor registry)
    - _sync_inner_tools() (tool registry)

    The new approach syncs all registries in one call.
    """
    logger.info("Initializing centralized registry system...")
    try:
        # Import from a new centralized location
        from structure.registries.core import ExecutorRegistry, ToolRegistry
        from structure.registries.manager import RegistryManager, sync_all_registries

        # Step 1: Import all executor modules (triggers @register_executor decorators)
        logger.info("Auto-discovering executor modules...")
        ExecutorRegistry.discover_and_import_executors()

        # Step 2: Get a registry manager instance
        manager = RegistryManager.get_instance()

        # Step 3: Register registries if not already registered
        if not manager.is_registered(ToolRegistry):
            manager.register_registry(ToolRegistry)
        if not manager.is_registered(ExecutorRegistry):
            manager.register_registry(ExecutorRegistry)

        # Step 4: Get registry instances and auto-discover tools
        tool_registry = manager.get_registry(ToolRegistry)
        executor_registry = manager.get_registry(ExecutorRegistry)

        # Auto-discover and register InnerTool subclasses
        if config.discover_inner_tools:
            logger.info("Auto-discovering tool modules from filesystem...")
            tool_registry.discover_and_register_tools()
        else:
            logger.info("Skipping tool discovery from filesystem (discover_inner_tools=false)")

        # Log current state
        tool_count = len(tool_registry.list_tools())
        executor_count = len(executor_registry.list_templates())
        logger.info(
            f"   📊 In-memory state: {tool_count} tools, {executor_count} executors"
        )

        # Step 5: Sync all registries to database in one call
        logger.info("   💾 Syncing all registries to database...")
        async with get_session("structure") as session:
            await sync_all_registries(session)

        # Step 6: Verify and report
        logger.info("✅ Registry system initialized successfully")
        logger.info(f"   ✓ ToolRegistry: {tool_count} tools registered")
        logger.info(f"   ✓ ExecutorRegistry: {executor_count} executors registered")

        # Get statistics
        stats = manager.get_statistics()
        logger.info(f"   📈 Registry statistics: {stats}")

    except Exception as e:
        logger.error(f" Registry initialization failed: {e}", exc_info=True)
        # Don't raise exception, allow app to continue


async def _initialize_default_context_paths() -> None:
    """Ensure all workspaces have the default context path skeleton.

    Runs at API startup — idempotent, safe to call repeatedly.
    """
    logger.info("Seeding default context paths...")
    try:
        async with get_session("structure") as session:
            await ensure_default_context_paths(db=session)
    except Exception as e:
        logger.error("Default context path seeding failed: %s", e, exc_info=True)


class ApplicationBootstrap:
    """Application Initialization Manager (v2)"""

    def __init__(self, config: BootstrapConfig | None = None):
        """
        Initialize Bootstrap

        Args:
            config: Initialization configuration, uses default if None
        """
        from structure.config.factory import get_settings

        self.config = config or BootstrapConfig()
        self.settings = get_settings()
        self.redis_client = None
        self.storage = None

    async def initialize(self) -> None:
        """
        Execute the complete initialization flow

        Initialize components in the correct order
        """
        logger.info("=" * 60)
        logger.info("Application initialization started (v2)")
        logger.info("=" * 60)

        # Step 1: Logging system (if needed)
        if self.config.init_logging:
            _setup_logging()

        # Step 2: Database connection
        if self.config.init_database:
            await _initialize_databases()

        # Step 3: Database table creation (API service only)
        if self.config.create_tables:
            await _create_tables()

        # Step 4: Redis connection
        if self.config.init_redis:
            await self._initialize_redis()

        # Step 5: Create an admin user (API service only)
        if self.config.create_admin_user:
            await self._create_admin_user()

        # Step 6: NEW: Unified registry initialization
        if self.config.init_registries:
            await _initialize_registries(self.config)

        # Step 7: Storage backend
        if self.config.init_storage:
            await self._init_storage_backend()

        # Step 8: Seed default context paths in all workspaces (API only)
        if self.config.init_context_paths:
            await _initialize_default_context_paths()

        logger.info("=" * 60)
        logger.info("✅ Application initialization completed (v2)")
        logger.info("=" * 60)

    async def cleanup(self) -> None:
        """Cleanup all resources"""
        logger.info("=" * 60)
        logger.info("🧹 Resource cleanup started")
        logger.info("=" * 60)

        # Shutdown Redis connection
        if self.config.init_redis:
            await _shutdown_redis()

        # Shutdown database connections
        if self.config.init_database:
            await _shutdown_databases()

        logger.info("=" * 60)
        logger.info("✅ Resource cleanup completed")
        logger.info("=" * 60)

    # ========================================================================
    # Private methods - individual initialization steps
    # ========================================================================

    async def _initialize_redis(self) -> None:
        """Initialize Redis connection"""
        logger.info("🔴 Initializing Redis connection...")
        try:
            from structure.middleware.cache_middleware import (
                get_redis_client,
                init_redis_client,
            )

            await init_redis_client()
            self.redis_client = get_redis_client(is_async=True)

            if not self.redis_client:
                raise RuntimeError("Redis client initialization returned None")
            redis_conf = self.settings.redis
            if not redis_conf:
                logger.error("Redis configuration is missing")
                return
            logger.info(f"✅ Redis connected: {redis_conf.host}:{redis_conf.port}")
        except Exception as e:
            logger.error(f" Redis initialization failed: {e}")
            raise

    async def _init_storage_backend(self) -> None:
        """Initialize storage backend (for Worker)"""
        from structure.extensions.storage.global_storage import init_global_s3_storage

        rustfs_conf = self.settings.rustfs
        if not rustfs_conf:
            logger.error("RustFS configuration is missing")
            return
        init_global_s3_storage(
            endpoint_url=rustfs_conf.endpoint,
            bucket=rustfs_conf.bucket,
            access_key=rustfs_conf.access_key,
            secret_key=rustfs_conf.secret_key,
            use_ssl=rustfs_conf.secure,
        )
        self.storage = get_global_s3_storage()
        logger.info("Storage initialized successfully")

    async def _create_admin_user(self) -> None:
        """Create admin user"""
        logger.info("Checking admin user...")
        try:
            async with get_session("structure") as session:
                # Check default tenant
                result = await session.execute(
                    select(Tenant).where(Tenant.name == "default")
                )
                tenant = result.scalar_one_or_none()

                if not tenant:
                    logger.info("   Creating default tenant...")
                    tenant = Tenant(name="default", description="Default tenant")
                    session.add(tenant)
                    await session.commit()
                    await session.refresh(tenant)
                    logger.info("   Default tenant created")
                else:
                    logger.info("   Default tenant exists")

                # Check admin user
                admin_username = self.settings.auth.admin_username
                result = await session.execute(
                    select(User).where(User.username == admin_username)
                )
                admin_user = result.scalar_one_or_none()

                if admin_user:
                    logger.info(f"   ✅ Admin user exists: {admin_username}")
                    return

                # Create admin user
                logger.info(f"   Creating admin user: {admin_username}")
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

                # Update tenant admin ID
                tenant.admin_id = admin_user.id
                session.add(tenant)

                await session.commit()
                await session.refresh(admin_user)

                logger.info("   ✅ Admin user created")
                logger.info(f"      Username: {admin_username}")
                logger.info(f"      Email: {admin_user.email}")
                logger.info(f"      Role: {admin_user.role}")
        except Exception as e:
            logger.error(f"❌ Admin user creation failed: {e}")
            # Don't raise exception, allow app to continue

    def get_redis_client(self):
        """Get Redis client (for Worker)"""
        return self.redis_client


# ============================================================================
# Predefined configuration templates
# ============================================================================


def get_api_bootstrap_config() -> BootstrapConfig:
    """
    API service initialization configuration

    Note:
    - create_tables=False: Tables managed by Alembic
    - create_admin_user=True: API responsible for user creation
    - init_registries=True: ⭐ NEW unified registry initialization
    - discover_inner_tools=False: Tools imported via MCP
    """
    return BootstrapConfig(
        init_logging=True,
        init_redis=True,
        init_database=True,
        create_tables=False,  # ⚠️  Tables managed by Alembic
        create_admin_user=True,  # API creates users
        init_registries=True,  # ⭐ NEW: Unified registry init
        init_storage=True,
        init_context_paths=True,  # Seed default context paths in all workspaces
        discover_inner_tools=False,  # API connects to MCP server for tools
    )


def get_worker_bootstrap_config() -> BootstrapConfig:
    """
    Worker service initialization configuration

    Note:
    - create_tables=False: Tables managed by Alembic
    - create_admin_user=False: Worker doesn't create users
    - init_registries=True: ⭐ NEW unified registry initialization
    - discover_inner_tools=False: Tools imported via MCP
    """
    return BootstrapConfig(
        init_logging=True,
        init_redis=True,
        init_database=True,
        create_tables=False,  # Tables managed by Alembic
        create_admin_user=False,  # Worker doesn't create users
        init_registries=True,  # ⭐ NEW: Unified registry init
        init_storage=True,
        discover_inner_tools=False,
    )


def get_alembic_bootstrap_config() -> BootstrapConfig:
    """
    Alembic initialization configuration

    Note:
    - create_tables=False: Tables managed by Alembic
    - init_registries=False: Alembic doesn't need registries
    """
    return BootstrapConfig(
        init_logging=False,
        init_redis=False,
        init_database=True,
        create_tables=False,
        create_admin_user=False,
        init_registries=False,
        init_storage=False,
    )


def get_celery_bootstrap_config() -> BootstrapConfig:
    """Celery initialization configuration"""
    return BootstrapConfig(
        init_logging=False,
        init_redis=False,
        init_database=True,
        create_tables=False,
        create_admin_user=False,
        init_registries=False,
        init_storage=True,
    )


def get_mcp_bootstrap_config() -> BootstrapConfig:
    """
    MCP service initialization configuration
    
    Needs database for tool discovery, and logging.
    """  # noqa: W293
    return BootstrapConfig(
        init_logging=True,
        init_redis=False,
        init_database=True,
        create_tables=False,
        create_admin_user=False,
        init_registries=True,  # Need registries to discover tools
        init_storage=False,
    )


async def bootstrap_alembic() -> ApplicationBootstrap:
    """Initialize Alembic service"""
    bootstrap = ApplicationBootstrap(get_alembic_bootstrap_config())
    await bootstrap.initialize()
    return bootstrap


async def bootstrap_api() -> ApplicationBootstrap:
    """Initialize API service"""
    bootstrap = ApplicationBootstrap(get_api_bootstrap_config())
    await bootstrap.initialize()
    return bootstrap


async def bootstrap_worker() -> ApplicationBootstrap:
    """Initialize Worker service"""
    bootstrap = ApplicationBootstrap(get_worker_bootstrap_config())
    await bootstrap.initialize()
    return bootstrap


async def bootstrap_celery() -> ApplicationBootstrap:
    """Initialize Celery service"""
    bootstrap = ApplicationBootstrap(get_celery_bootstrap_config())
    await bootstrap.initialize()
    return bootstrap


async def bootstrap_mcp() -> ApplicationBootstrap:
    """Initialize MCP service"""
    bootstrap = ApplicationBootstrap(get_mcp_bootstrap_config())
    await bootstrap.initialize()
    return bootstrap
