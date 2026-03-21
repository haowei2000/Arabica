# aiwen/config/base.py
import logging
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from aiwen.config.components.auth import AuthConfig
from aiwen.config.components.ollama import OllamaConfig
from aiwen.config.components.openai import OpenAIConfig
from aiwen.config.components.postgres import PostgresConfig
from aiwen.config.components.redis import RedisConfig
from aiwen.config.components.rustfs import RustfsConfig

# Calculate PROJECT_ROOT directly to avoid circular import via core/__init__.py
# src/aiwen/config/base.py -> src/aiwen/config -> src/aiwen (PROJECT_ROOT)
PROJECT_ROOT = Path(__file__).parent.parent

logger = logging.getLogger(__name__)


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
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
    agent_task_name: str = "agent:stream"

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
        if not self.redis:
            logging.warning(
                "Redis configuration is not set. Cache features may not work properly."
            )
        if not self.auth:
            logging.warning(
                "Auth configuration is not set. Authentication features may not work properly."
            )

    postgres: PostgresConfig
    redis: RedisConfig
    auth: AuthConfig
    ollama: OllamaConfig | None = None
    openai: OpenAIConfig | None = None
    rustfs: RustfsConfig
