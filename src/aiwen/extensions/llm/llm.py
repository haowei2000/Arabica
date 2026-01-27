import logging

from langchain_community.cache import RedisCache
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from aiwen.config.factory import get_settings
from aiwen.middleware.cache_middleware import get_redis_client

logger = logging.getLogger(__name__)


def get_llm(
    name: str, provider: str = "tongyi", add_cache: bool = False
) -> BaseChatModel:
    """
    Factory function to return langchain model instance based on model name and provider.

    Args:
        name (str): The name of the model.
        provider (str): The provider of the model. Defaults to "tongyi".
        add_cache (bool): Whether to add cache to the model. Defaults to False.

    Returns:
        BaseChatModel: Langchain model instance.

    根据模型名称和供应商返回langchain模型调用实例的工厂函数。

    参数:
        name (str): 模型名称。
        provider (str): 模型供应商。默认为"tongyi"。
        add_cache (bool): 是否给模型添加缓存。默认为False。

    返回:
        BaseChatModel: Langchain模型实例。
    """

    if add_cache:
        # Set up Redis cache if Redis URL is provided
        try:
            redis_client = get_redis_client(is_async=False)
            redis_cache = RedisCache(redis_client)
            logger.info("%s %s Redis cache initialized successfully", name, provider)
        except RuntimeError:
            # Redis not initialized, proceed without cache
            logger.warning("%s %s Redis cache not initialized", name, provider)
            redis_cache = None
        except Exception:
            # Other Redis error, proceed without cache
            logger.error("%s %s Redis cache initialization failed", name, provider)
            redis_cache = None
    else:
        redis_cache = None

    settings = get_settings()
    match provider:
        case "tongyi":
            # Get settings for Tongyi API key
            # Use OpenAI config if available, otherwise fall back to dashscope_api_key
            if settings.openai:
                api_key = settings.openai.api_key or settings.dashscope_api_key
                base_url = settings.openai.base_url
            else:
                api_key = settings.dashscope_api_key
                # Default to Alibaba DashScope compatible API
                base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

            return ChatOpenAI(
                model=name,
                api_key=api_key,  # type: ignore
                base_url=base_url,
                cache=redis_cache,
            )
        case "ollama":
            return ChatOllama(
                model=name, base_url=settings.ollama.base_url, cache=redis_cache
            )
        case _:
            raise ValueError(f"Unsupported provider: {provider}")
