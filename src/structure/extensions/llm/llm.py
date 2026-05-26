import logging

from langchain_community.cache import RedisCache
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from structure.config.factory import get_settings
from structure.middleware.cache_middleware import get_redis_client

logger = logging.getLogger(__name__)

# Cache LLM clients by (name, provider, add_cache) to avoid creating
# new HTTP connection pools on every call.
_llm_cache: dict[tuple[str, str, bool], BaseChatModel] = {}


def get_llm(
    name: str, provider: str = "openai", add_cache: bool = False
) -> BaseChatModel:
    """Return a cached LangChain model instance from the OPENAI__ env contract.

    Instances are cached by the resolved ``OPENAI__MODEL`` and provider so repeated
    calls with the same arguments reuse the underlying HTTP connection
    pool instead of creating a new one each time.

    Args:
        name: Deprecated model hint. The effective model is always ``OPENAI__MODEL``.
        provider: Model provider. Only OpenAI-compatible providers are supported.
        add_cache: Whether to attach a Redis response cache.

    Returns:
        Cached ``BaseChatModel`` instance.
    """
    _ = name
    settings = get_settings()
    openai_settings = settings.openai
    api_key = (openai_settings.api_key if openai_settings else "").strip()
    base_url = (openai_settings.base_url if openai_settings else "").strip()
    model = (openai_settings.model if openai_settings else "").strip()
    if not api_key or not base_url or not model:
        raise ValueError(
            "OPENAI__API_KEY, OPENAI__BASE_URL, and OPENAI__MODEL must be set"
        )

    cache_key = (model, provider, add_cache)
    if cache_key in _llm_cache:
        return _llm_cache[cache_key]

    if add_cache:
        try:
            redis_client = get_redis_client(is_async=False)
            redis_cache = RedisCache(redis_client)
            logger.info("%s %s Redis cache initialized successfully", model, provider)
        except RuntimeError:
            logger.warning("%s %s Redis cache not initialized", model, provider)
            redis_cache = None
        except Exception:
            logger.error("%s %s Redis cache initialization failed", model, provider)
            redis_cache = None
    else:
        redis_cache = None

    match provider:
        case "openai" | "custom":
            llm = ChatOpenAI(
                model=model,
                api_key=api_key,  # type: ignore
                base_url=base_url,
                cache=redis_cache,
            )
        case _:
            raise ValueError(f"Unsupported provider: {provider}")

    _llm_cache[cache_key] = llm
    logger.info("Created and cached LLM client: %s/%s", provider, name)
    return llm
