import logging

from langchain_community.cache import RedisCache
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from structure.config.factory import get_settings
from structure.middleware.cache_middleware import get_redis_client

logger = logging.getLogger(__name__)

# Cache LLM clients by (name, provider, add_cache) to avoid creating
# new HTTP connection pools on every call.
_llm_cache: dict[tuple[str, str, bool], BaseChatModel] = {}


def get_llm(
    name: str, provider: str = "tongyi", add_cache: bool = False
) -> BaseChatModel:
    """Return a cached LangChain model instance.

    Instances are cached by ``(name, provider, add_cache)`` so repeated
    calls with the same arguments reuse the underlying HTTP connection
    pool instead of creating a new one each time.

    Args:
        name: Model name.
        provider: Model provider. Defaults to ``"tongyi"``.
        add_cache: Whether to attach a Redis response cache.

    Returns:
        Cached ``BaseChatModel`` instance.
    """
    cache_key = (name, provider, add_cache)
    if cache_key in _llm_cache:
        return _llm_cache[cache_key]

    if add_cache:
        try:
            redis_client = get_redis_client(is_async=False)
            redis_cache = RedisCache(redis_client)
            logger.info("%s %s Redis cache initialized successfully", name, provider)
        except RuntimeError:
            logger.warning("%s %s Redis cache not initialized", name, provider)
            redis_cache = None
        except Exception:
            logger.error("%s %s Redis cache initialization failed", name, provider)
            redis_cache = None
    else:
        redis_cache = None

    settings = get_settings()
    match provider:
        case "tongyi":
            if settings.openai:
                api_key = settings.openai.api_key or settings.dashscope_api_key
                base_url = settings.openai.base_url
            else:
                api_key = settings.dashscope_api_key
                base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

            llm = ChatOpenAI(
                model=name,
                api_key=api_key,  # type: ignore
                base_url=base_url,
                cache=redis_cache,
            )
        case "ollama":
            llm = ChatOllama(
                model=name, base_url=settings.ollama.base_url, cache=redis_cache
            )
        case _:
            raise ValueError(f"Unsupported provider: {provider}")

    _llm_cache[cache_key] = llm
    logger.info("Created and cached LLM client: %s/%s", provider, name)
    return llm
