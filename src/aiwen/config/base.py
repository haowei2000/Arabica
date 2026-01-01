# aiwen/config/base.py
import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from aiwen.config.components.auth import AuthConfig
from aiwen.config.components.mysql import MysqlConfig
from aiwen.config.components.ollama import OllamaConfig
from aiwen.config.components.openai import OpenAIConfig
from aiwen.config.components.postgres import PostgresConfig
from aiwen.config.components.redis import RedisConfig
from aiwen.constants.path import PROJECT_ROOT

logger = logging.getLogger(__name__)


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / "src" / ".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="allow",
    )

    app_name: str = "Modular FastAPI App"
    DEBUG: bool = False
    mcp_http_enable: bool = False
    mcp_sse_enable: bool = False
    mcp_cache_enable: bool = False
    app_cache_enable: bool = False

    dashscope_api_key: str = Field(default="", description="DashScope API Key")
    env: str = "development"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.dashscope_api_key:
            logger.warning(
                "DashScope API Key is not set. Some features may not work properly."
            )
        if not self.postgres:
            logging.warning(
                "PostgreSQL configuration is not set. Database features may not work properly."
            )
        if not self.mysql:
            logging.warning(
                "MySQL configuration is not set. Database features may not work properly."
            )
        if not self.redis:
            logging.warning(
                "Redis configuration is not set. Cache features may not work properly."
            )
        if not self.auth:
            logging.warning(
                "Auth configuration is not set. Authentication features may not work properly."
            )

    postgres: PostgresConfig
    mysql: MysqlConfig
    redis: RedisConfig | None = None
    auth: AuthConfig | None = None
    ollama: OllamaConfig | None = None
    openai: OpenAIConfig | None = None
