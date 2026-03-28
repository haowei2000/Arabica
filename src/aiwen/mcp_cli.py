# ============================================================================
# mcp_server/app.py
# ============================================================================
"""MCP 应用核心实现"""

from contextlib import asynccontextmanager
import logging
import os

from aiwen.mcp_router.browser import browser_mcp, shutdown_browser_sessions
from aiwen.mcp_router.nl2sql import nl2sql_mcp
from aiwen.mcp_router.inner_tools import register_inner_tools_to_mcp
from dotenv import load_dotenv
from fastmcp import FastMCP
import fastmcp.server.middleware
from fastmcp.server.middleware.caching import (
    CallToolSettings,
    ListToolsSettings,
    ResponseCachingMiddleware,
)
from fastmcp.server.middleware.logging import LoggingMiddleware
from key_value.aio.stores.redis import RedisStore
import uvicorn

from aiwen.config.factory import get_settings
from aiwen.core.bootstrap import bootstrap_mcp
from aiwen.extensions.logger import setup_logging

# 初始化日志
setup_logging()
logger = logging.getLogger("mcp")


class SimpleLoggingMiddleware(fastmcp.server.middleware.Middleware):
    async def on_message(
        self, context: fastmcp.server.middleware.MiddlewareContext, call_next
    ):
        logger.info(f"Processing {context.method} from {context.source}")

        try:
            result = await call_next(context)
            logger.info(f"Completed {context.method}")
            return result
        except Exception as e:
            logger.error(f"Failed {context.method}: {e}")
            raise


def _initialize_cache_middleware(settings) -> ResponseCachingMiddleware:
    """初始化缓存中间件"""
    try:
        cache_store = RedisStore(
            host=settings.redis.host,
            port=settings.redis.port,
            db=settings.redis.db_session,
            password=settings.redis.password,
        )
        logger.debug(
            f"Cache middleware initialized: {settings.redis.host}:{settings.redis.port}"
        )
        return ResponseCachingMiddleware(
            cache_storage=cache_store,
            list_tools_settings=ListToolsSettings(ttl=30),
            call_tool_settings=CallToolSettings(included_tools=[]),
        )
    except Exception as e:
        logger.error(f"Failed to initialize cache middleware: {e}", exc_info=True)
        raise


def _setup_mcp_server() -> FastMCP:
    """配置并创建 MCP 服务器"""
    settings = get_settings()

    mcp_instance = FastMCP("Aiwen MCP", lifespan=lifespan)
    mcp_instance.mount(nl2sql_mcp)
    mcp_instance.mount(browser_mcp)
    
    # Register all discovered inner tools
    register_inner_tools_to_mcp(mcp_instance)
    
    mcp_instance.add_middleware(SimpleLoggingMiddleware())
    if settings.mcp_cache_enable:
        mcp_instance.add_middleware(_initialize_cache_middleware(settings))
    # 添加日志中间件，支持载荷记录
    mcp_instance.add_middleware(
        LoggingMiddleware(include_payloads=True, max_payload_length=1000)
    )

    logger.info("MCP server configured: AIWEN MCP")
    return mcp_instance


@asynccontextmanager
async def lifespan(app: FastMCP):
    """管理应用生命周期 - 使用统一的Bootstrap"""
    bootstrap = None
    try:
        logger.info("🔌 MCP starting up...")
        # ⚠️ Force inner tool discovery on for the MCP server so it can expose them
        os.environ["AIWEN_SKIP_INNER_TOOL_DISCOVERY"] = "false"
        
        # 使用统一的初始化流程
        bootstrap = await bootstrap_mcp()
        logger.info("✅ MCP startup complete")
        yield
    except Exception as e:
        logger.error(f"❌ Error during startup: {e}", exc_info=True)
        raise
    finally:
        # 使用统一的清理流程
        logger.info("🧹 MCP shutting down...")
        if bootstrap:
            try:
                await bootstrap.cleanup()
            except Exception as e:
                logger.error(f"⚠️  Error during cleanup: {e}", exc_info=True)
        try:
            await shutdown_browser_sessions()
        except Exception as e:
            logger.error(f"⚠️  Error during browser cleanup: {e}", exc_info=True)
        logger.info("✅ MCP shutdown complete")


# 创建全局 MCP 实例
def _create_mcp_instance() -> FastMCP:
    """创建 MCP 服务器实例"""
    return _setup_mcp_server()


# 延迟加载 MCP 实例
_mcp_instance = None


def get_mcp() -> FastMCP:
    """获取或创建 MCP 实例（单例）"""
    global _mcp_instance
    if _mcp_instance is None:
        _mcp_instance = _create_mcp_instance()
    return _mcp_instance


# 在模块导入时创建 MCP 实例
mcp = _create_mcp_instance()


def run() -> None:
    """
    启动 MCP 服务器

    配置会在 aiwen.config.factory 模块导入时自动加载，
    不需要在这里手动调用 load_dotenv()
    """
    try:
        mcp_instance = _setup_mcp_server()
        logger.info(
            f"Starting MCP Server on {os.environ.get('AIWEN_MCP_HOST')}:{os.environ.get('AIWEN_MCP_PORT')}..."
        )
        uvicorn.run(
            mcp_instance.http_app(),
            host=os.environ.get("AIWEN_MCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("AIWEN_MCP_PORT", 9000)),
            log_level="error",
        )
    except Exception as e:
        logger.error(f"Failed to start MCP Server: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    run()
