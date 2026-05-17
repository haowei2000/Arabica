#!/usr/bin/env python3
"""
模块名称: 配置工厂模块

功能描述:
    该模块负责根据不同的运行环境加载相应的配置文件，并提供全局配置访问接口。
    支持开发、测试、生产等多种环境配置，通过 ENV 环境变量进行切换。
    使用 lru_cache 缓存机制确保配置单例模式，提高访问效率。

    统一配置管理：
    - 所有服务（API、Worker、MCP）都通过 get_settings() 获取配置
    - 配置文件自动从项目根目录的 src/.env 加载
    - 不需要在各个服务中重复调用 load_dotenv()

使用示例:
    from structure.config.factory import get_settings
    settings = get_settings()
    print(settings.postgres.structure_dbname)
"""

from functools import lru_cache
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from .base import AppSettings

logger = logging.getLogger(__name__)

# 确定项目根目录和 .env 文件位置
# 这个文件在 src/structure/config/factory.py
# 项目根目录是 src/structure/config/../../../ = project_root
_current_file = Path(__file__).resolve()  # src/structure/config/factory.py
_project_root = _current_file.parent.parent.parent.parent  # 往上4层到项目根目录

# 优先级顺序查找 .env 文件
_env_search_paths = [
    _project_root / "src" / ".env",  # src/.env (标准位置)
    _project_root / ".env",  # 项目根目录 .env
    Path.cwd() / ".env",  # 当前工作目录 .env
]


def _load_environment_file() -> None:
    """
    智能加载环境变量文件

    按优先级顺序查找并加载 .env 文件：
    1. src/.env (标准位置)
    2. 项目根目录 .env
    3. 当前工作目录 .env

    根据 ENV 环境变量选择特定环境的配置文件。
    """
    # 获取环境类型
    env = os.getenv("ENV", "development").lower()

    # 根据环境选择文件名
    env_filenames = {
        "development": ".env",
        "production": ".env.prod",
        "testing": ".env.test",
    }
    base_filename = env_filenames.get(env, ".env")

    # 尝试按优先级查找并加载
    loaded = False
    for search_path in _env_search_paths:
        # 尝试特定环境文件（如 .env.prod）
        env_file = (
            search_path.parent / base_filename
            if base_filename != ".env"
            else search_path
        )

        if env_file.exists():
            load_dotenv(env_file, override=False)
            logger.info(f"✅ Configuration loaded from: {env_file}")
            loaded = True
            break

    if not loaded:
        logger.warning(
            f"⚠️  No .env file found in search paths: {[str(p) for p in _env_search_paths]}"
        )
        logger.warning("   Using system environment variables only")


# 在模块导入时立即加载环境变量
# 这样确保在任何地方调用 get_settings() 时配置已经加载
_load_environment_file()


@lru_cache
def get_settings() -> AppSettings:
    """
    获取应用配置（单例模式）

    使用 lru_cache 确保配置只加载一次，后续调用直接返回缓存的实例。
    环境变量已在模块导入时加载，此函数只负责创建和返回配置对象。

    Returns:
        AppSettings: 应用配置对象

    Example:
        >>> from structure.config.factory import get_settings
        >>> settings = get_settings()
        >>> print(settings.postgres.structure_dbname)
        'structure'
    """
    return AppSettings()
