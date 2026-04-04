from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
import datetime as _dt
import decimal as _decimal
import json
import logging
from typing import Any
import uuid as _uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)


def _json_default(obj: Any) -> Any:
    """
    JSON serializer for objects not serializable by default json code.

    Handles:
    - UUID objects -> str
    - datetime/date objects -> ISO format string
    - Decimal objects -> float

    Args:
        obj: Object to serialize

    Returns:
        Serializable representation of the object

    Raises:
        TypeError: If object type is not supported
    """
    if isinstance(obj, _uuid.UUID):
        return str(obj)
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()
    if isinstance(obj, _decimal.Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# 全局缓存
_engines: dict[str, AsyncEngine] = {}
_bases: dict[str, type[DeclarativeBase]] = {}
_sessionmakers: dict[str, async_sessionmaker[AsyncSession]] = {}
_initialized = False  # 添加初始化标志
# 缓存固定依赖函数，确保 Depends(...) 获取到同一函数对象
_dependency_functions: dict[str, Callable[[], AsyncGenerator[AsyncSession, None]]] = {}

# ==============================
# 配置常量
# ==============================
DEFAULT_POOL_CONFIG = {
    "pool_size": 5,
    "max_overflow": 10,
    "pool_timeout": 10,  # Reduced from 30s for faster failure
    "pool_recycle": 1800,  # 30 minutes instead of 1 hour
    "pool_pre_ping": False,  # Disabled to reduce latency (rely on pool_recycle)
}

# 数据库特定配置
DB_SPECIFIC_CONFIG = {
    "mysql": {
        "pool_size": 5,
        "max_overflow": 10,
        "connect_args": {
            "charset": "utf8mb4",
        },
    },
    "postgresql": {
        "pool_size": 5,
        "max_overflow": 10,
    },
}


def _ensure_registered():
    """确保所有数据库已注册（懒加载）"""
    from structure.config.factory import get_settings

    global _initialized
    if _initialized:
        return  # 已初始化

    settings = get_settings()
    binds = {}
    binds.update(settings.postgres.structure_sqlalchemy_bind)

    for bind_name, url in binds.items():
        register_database(bind_name, url)

    _initialized = True
    logger.info(
        f"Database initialization complete. Registered {len(_engines)} databases"
    )


def register_database(bind_name: str, url: str, pool_config: dict | None = None):
    """
    注册单个数据库

    Args:
        bind_name: 数据库绑定名称
        url: 数据库连接 URL
        pool_config: 自定义连接池配置（可选）
    """
    if bind_name in _engines:
        logger.warning(f"Database '{bind_name}' already registered, skipping")
        return

    # 识别数据库类型
    db_type = None
    if "mysql" in url.lower():
        db_type = "mysql"
    elif "postgresql" in url.lower() or "postgres" in url.lower():
        db_type = "postgresql"

    # 合并配置
    final_config = DEFAULT_POOL_CONFIG.copy()
    if db_type and db_type in DB_SPECIFIC_CONFIG:
        final_config.update(DB_SPECIFIC_CONFIG[db_type])
    if pool_config:
        final_config.update(pool_config)

    # 提取 connect_args
    connect_args = final_config.pop("connect_args", {})

    try:
        from structure.config.factory import get_settings

        engine = create_async_engine(
            url,
            echo=get_settings().DEBUG,
            **final_config,
            connect_args=connect_args,
            json_serializer=lambda v: json.dumps(v, default=_json_default),
        )
        _engines[bind_name] = engine

        # 创建动态 Base 类（SQLAlchemy 2.0 风格）
        class DynamicBase(DeclarativeBase):
            pass

        # 设置 __name__ 便于调试
        DynamicBase.__name__ = f"{bind_name.capitalize()}Base"
        _bases[bind_name] = DynamicBase

        session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,  # 明确禁用 autocommit
        )
        _sessionmakers[bind_name] = session_factory

        # 屏蔽密码信息
        safe_url = _mask_url(url)
        logger.info(f"✓ Registered database: {bind_name} -> {safe_url}")
        logger.debug(
            f"  Pool: size={final_config.get('pool_size')}, "
            f"max_overflow={final_config.get('max_overflow')}"
        )

    except Exception as e:
        logger.error(f"✗ Failed to register database '{bind_name}': {e}")
        raise


def _mask_url(url: str) -> str:
    """隐藏 URL 中的密码"""
    if "@" in url:
        parts = url.split("@")
        prefix = parts[0]
        suffix = "@".join(parts[1:])

        if ":" in prefix:
            user_part = prefix.split("://")
            if len(user_part) >= 2:
                protocol = user_part[0]
                credentials = user_part[1].split(":")
                if len(credentials) >= 2:
                    prefix = f"{protocol}://{credentials[0]}:****"

        return f"{prefix}@{suffix[:50]}{'...' if len(suffix) > 50 else ''}"
    return url[:50] + ("..." if len(url) > 50 else "")


# ==============================
# 依赖项：直接用于 FastAPI Depends
# ==============================
def _create_session_dependency(bind_name: str):
    async def _session_generator() -> AsyncGenerator[AsyncSession, None]:
        _ensure_registered()
        if bind_name not in _sessionmakers:
            available = ", ".join(_sessionmakers.keys())
            raise ValueError(
                f"Database '{bind_name}' is not configured. Available: {available}"
            )
        session_factory = _sessionmakers[bind_name]
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception as e:
                await session.rollback()
                # HTTPException is normal FastAPI flow control (4xx/5xx responses),
                # not a database error — skip noisy error logging for it.
                from fastapi import HTTPException as _HTTPException

                if not isinstance(e, _HTTPException):
                    logger.error(
                        f"Database session error in '{bind_name}': {e}", exc_info=True
                    )
                raise
            finally:
                await session.close()

    return _session_generator


def get_db_session(bind_name: str = "primary"):
    """返回一个固定（可缓存）的依赖函数对象。更推荐直接使用命名依赖：Depends(get_structure_db) / Depends(get_mes_db)。"""
    dep = _dependency_functions.get(bind_name)
    if dep is None:
        dep = _create_session_dependency(bind_name)
        _dependency_functions[bind_name] = dep
    return dep


# ==============================
# 上下文管理器（用于非 FastAPI 场景）
# ==============================
@asynccontextmanager
async def get_session(bind_name: str = "primary") -> AsyncGenerator[AsyncSession, None]:
    """
    上下文管理器方式获取 session（适用于脚本、任务等非 FastAPI 场景）

    Args:
        bind_name: 数据库绑定名称

    Usage:
        async with get_session("primary") as session:
            result = await session.execute(select(User))
            users = result.scalars().all()

    Yields:
        AsyncSession 实例
    """
    _ensure_registered()

    if bind_name not in _sessionmakers:
        available = ", ".join(_sessionmakers.keys())
        raise ValueError(
            f"Database '{bind_name}' is not configured. Available: {available}"
        )

    session_factory = _sessionmakers[bind_name]
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Session error in '{bind_name}': {e}", exc_info=True)
            raise
        finally:
            await session.close()


# ==============================
# 获取 Base（用于定义模型）
# ==============================
def get_base(bind_name: str) -> type[DeclarativeBase]:
    """
    获取指定数据库的 Base 类

    Args:
        bind_name: 数据库绑定名称

    Usage:
        Base = get_base("primary")

        class User(Base):
            __tablename__ = "users"
            id = Column(Integer, primary_key=True)

    Returns:
        DeclarativeBase 子类
    """
    _ensure_registered()
    if bind_name not in _bases:
        available = ", ".join(_bases.keys())
        raise ValueError(
            f"Base for database '{bind_name}' not found. Available: {available}"
        )
    return _bases[bind_name]


# ==============================
# 获取引擎（高级用法）
# ==============================
def get_engine(bind_name: str = "primary") -> AsyncEngine:
    """
    获取原始引擎对象（用于高级操作）

    Args:
        bind_name: 数据库绑定名称

    Returns:
        AsyncEngine 实例

    Note:
        通常不需要直接使用引擎，使用 session 即可
    """
    _ensure_registered()

    if bind_name not in _engines:
        available = ", ".join(_engines.keys())
        raise ValueError(f"Engine for '{bind_name}' not found. Available: {available}")

    return _engines[bind_name]


# ==============================
# 健康检查
# ==============================
async def check_database_health(bind_name: str = "primary") -> dict:
    """
    检查数据库连接健康状态

    Args:
        bind_name: 数据库绑定名称

    Returns:
        健康状态字典
    """
    _ensure_registered()

    if bind_name not in _engines:
        available = ", ".join(_engines.keys())
        return {
            "status": "error",
            "input": f"Database '{bind_name}' not found. Available: {available}",
        }

    engine = _engines[bind_name]

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

        # 通过 sync_engine.pool 获取队列池指标，避免类型告警
        sync_engine = getattr(engine, "sync_engine", None)
        pool = getattr(sync_engine, "pool", None)

        def _pool_metric(name: str):
            func = getattr(pool, name, None)
            try:
                return int(func()) if callable(func) else None
            except Exception:
                return None

        pool_info = {
            "size": _pool_metric("size"),
            "checked_in": _pool_metric("checkedin"),
            "checked_out": _pool_metric("checkedout"),
            "overflow": _pool_metric("overflow"),
        }

        return {
            "status": "healthy",
            "bind_name": bind_name,
            "pool": pool_info,
        }
    except Exception as e:
        logger.error(f"Health check failed for '{bind_name}': {e}")
        return {
            "status": "unhealthy",
            "bind_name": bind_name,
            "error": str(e),
        }


async def check_all_databases() -> dict:
    """
    检查所有已注册数据库的健康状态

    Returns:
        所有数据库的健康状态字典
    """
    _ensure_registered()

    results = {}
    for bind_name in _engines:
        results[bind_name] = await check_database_health(bind_name)

    return results


# ==============================
# 清理资源
# ==============================
async def dispose_all():
    """
    关闭所有数据库连接（应用关闭时调用）

    Usage (FastAPI):
        @app.on_event("shutdown")
        async def shutdown():
            await dispose_all()

    Usage (FastMCP):
        @mcp.lifespan()
        async def lifespan():
            yield
            await dispose_all()
    """
    global _initialized

    for bind_name, engine in _engines.items():
        try:
            await engine.dispose()
            logger.info(f"✓ Disposed database: {bind_name}")
        except Exception as e:
            logger.error(f"✗ Error disposing '{bind_name}': {e}")

    _engines.clear()
    _bases.clear()
    _sessionmakers.clear()
    _initialized = False

    logger.info("All database connections disposed")


# ==============================
# 只读会话（性能优化）
# ==============================
@asynccontextmanager
async def get_readonly_session(
    bind_name: str = "primary",
) -> AsyncGenerator[AsyncSession, None]:
    """
    只读 session（适用于纯查询场景，不会自动提交）

    Args:
        bind_name: 数据库绑定名称

    Usage:
        async with get_readonly_session() as session:
            result = await session.execute(select(User))
            users = result.scalars().all()

    Yields:
        AsyncSession 实例
    """
    _ensure_registered()

    if bind_name not in _sessionmakers:
        raise ValueError(f"Database '{bind_name}' is not configured")

    session_factory = _sessionmakers[bind_name]
    async with session_factory() as session:
        try:
            yield session
            # 只读不需要提交
        except Exception as e:
            # 只读也不需要回滚，但记录错误
            logger.error(f"Readonly session error in '{bind_name}': {e}")
            raise
        finally:
            await session.close()


# ==============================
# 批量操作辅助函数
# ==============================
async def bulk_insert_objects(
    session: AsyncSession, objects: list, batch_size: int = 1000
):
    """
    批量插入对象（分批处理避免内存溢出）

    Args:
        session: 数据库会话
        objects: ORM 对象列表
        batch_size: 每批次大小

    Usage:
        async with get_session() as session:
            users = [User(name=f"user_{i}") for i in range(10000)]
            await bulk_insert_objects(session, users, batch_size=500)
    """
    total = len(objects)
    for i in range(0, total, batch_size):
        batch = objects[i : i + batch_size]
        session.add_all(batch)
        await session.flush()
        logger.debug(f"Inserted batch {i // batch_size + 1}: {len(batch)} records")

    await session.commit()
    logger.info(f"Bulk insert completed: {total} records")


# ==============================
# 工具函数
# ==============================
def list_registered_databases() -> list[str]:
    """
    列出所有已注册的数据库名称

    Returns:
        数据库名称列表
    """
    _ensure_registered()
    return list(_engines.keys())


def is_database_registered(bind_name: str) -> bool:
    """
    检查数据库是否已注册

    Args:
        bind_name: 数据库绑定名称

    Returns:
        是否已注册
    """
    _ensure_registered()
    return bind_name in _engines


# 命名固定依赖（推荐使用，无需括号）
get_primary_db = get_db_session("primary")
get_structure_db = get_db_session("structure")
get_mes_db = get_db_session("mes")
get_dify_db = get_db_session("dify")
