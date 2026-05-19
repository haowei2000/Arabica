"""Seed default LLM models from environment configuration."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from structure.config.components.openai import OpenAIConfig
from structure.core.constants.llm import MASKED_API_KEY_REF
from structure.models.llm.chat_model import ChatModel

ENV_OPENAI_META_SOURCE = "env_openai_default"


def _normalize(value: str | None) -> str:
    return (value or "").strip()


def has_usable_chat_credentials(model: ChatModel | None) -> bool:
    """Return whether a chat model can be used by the default executor."""
    if model is None or not model.enabled:
        return False

    if model.provider == "ollama":
        return True

    api_key = _normalize(model.api_key_ref)
    base_url = _normalize(model.base_url)
    return bool(api_key and api_key != MASKED_API_KEY_REF and base_url)


async def get_enabled_default_chat_model(db: AsyncSession) -> ChatModel | None:
    """Return the enabled default chat model, if one exists."""
    result = await db.execute(
        select(ChatModel)
        .where(ChatModel.is_default.is_(True), ChatModel.enabled.is_(True))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_env_openai_chat_model(
    db: AsyncSession,
    *,
    model_id: str,
) -> ChatModel | None:
    result = await db.execute(
        select(ChatModel)
        .where(
            ChatModel.is_system.is_(True),
            ChatModel.provider == "openai",
            ChatModel.model_id == model_id,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def ensure_openai_default_chat_model(
    db: AsyncSession,
    openai_config: OpenAIConfig | None,
) -> ChatModel | None:
    """Create or repair a usable default chat model from ``OPENAI__*`` settings.

    A valid admin-selected default model is left alone. The env model becomes
    default only when no usable default exists, which keeps manual admin choices
    intact while making a fresh deployment work from ``.env`` alone.
    """
    api_key = _normalize(openai_config.api_key if openai_config else None)
    base_url = _normalize(openai_config.base_url if openai_config else None)
    model_id = _normalize(openai_config.model if openai_config else None)

    if not api_key or not base_url or not model_id:
        return None

    current_default = await get_enabled_default_chat_model(db)
    if has_usable_chat_credentials(current_default):
        if (
            current_default
            and current_default.is_system
            and current_default.provider == "openai"
            and current_default.model_id == model_id
        ):
            current_default.name = "System OpenAI Chat"
            current_default.description = (
                "Default chat model seeded from OPENAI__* environment settings."
            )
            current_default.base_url = base_url
            current_default.api_key_ref = api_key
            current_default.meta = {
                **(current_default.meta or {}),
                "source": ENV_OPENAI_META_SOURCE,
            }
            await db.flush()
        return current_default

    env_model = await _get_env_openai_chat_model(db, model_id=model_id)
    if env_model is None:
        env_model = ChatModel(
            name="System OpenAI Chat",
            description="Default chat model seeded from OPENAI__* environment settings.",
            user_id=None,
            provider="openai",
            model_id=model_id,
            base_url=base_url,
            api_key_ref=api_key,
            supports_vision=False,
            supports_function_call=True,
            supports_streaming=True,
            default_temperature=0.7,
            currency="USD",
            is_system=True,
            is_default=True,
            enabled=True,
            meta={"source": ENV_OPENAI_META_SOURCE},
        )
        db.add(env_model)
    else:
        env_model.name = "System OpenAI Chat"
        env_model.description = (
            "Default chat model seeded from OPENAI__* environment settings."
        )
        env_model.base_url = base_url
        env_model.api_key_ref = api_key
        env_model.enabled = True
        env_model.is_system = True
        env_model.meta = {**(env_model.meta or {}), "source": ENV_OPENAI_META_SOURCE}

    await db.execute(
        update(ChatModel)
        .where(ChatModel.is_default.is_(True))
        .values(is_default=False)
    )
    env_model.is_default = True
    await db.flush()
    return env_model
