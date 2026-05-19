"""Unit tests for environment-backed default chat model seeding."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from structure.core.constants.llm import MASKED_API_KEY_REF
from structure.models.llm.chat_model import ChatModel
from structure.services.llm.env_model_seed import ensure_openai_default_chat_model


def result_for(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def openai_config(**overrides):
    defaults = {
        "api_key": "env-key",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_chat_model(**overrides) -> ChatModel:
    defaults = {
        "name": "Existing",
        "description": None,
        "user_id": None,
        "provider": "openai",
        "model_id": "gpt-4.1-mini",
        "base_url": "https://api.openai.com/v1",
        "api_key_ref": "stored-key",
        "supports_vision": False,
        "supports_function_call": True,
        "supports_streaming": True,
        "currency": "USD",
        "is_system": True,
        "is_default": True,
        "enabled": True,
    }
    defaults.update(overrides)
    return ChatModel(**defaults)


@pytest.mark.asyncio
async def test_ensure_openai_default_chat_model_creates_env_default_when_missing():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            result_for(None),
            result_for(None),
            MagicMock(),
        ]
    )

    model = await ensure_openai_default_chat_model(db, openai_config())

    assert model is db.add.call_args.args[0]
    assert model.name == "System OpenAI Chat"
    assert model.provider == "openai"
    assert model.model_id == "gpt-4.1-mini"
    assert model.base_url == "https://api.openai.com/v1"
    assert model.api_key_ref == "env-key"
    assert model.is_system is True
    assert model.is_default is True
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_openai_default_chat_model_keeps_valid_admin_default():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    existing = make_chat_model(
        is_system=False,
        provider="custom",
        model_id="custom-model",
    )
    db.execute = AsyncMock(return_value=result_for(existing))

    model = await ensure_openai_default_chat_model(db, openai_config())

    assert model is existing
    assert existing.api_key_ref == "stored-key"
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_openai_default_chat_model_repairs_masked_default():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    masked = make_chat_model(api_key_ref=MASKED_API_KEY_REF, base_url="https://old")
    db.execute = AsyncMock(
        side_effect=[
            result_for(masked),
            result_for(masked),
            MagicMock(),
        ]
    )

    model = await ensure_openai_default_chat_model(db, openai_config())

    assert model is masked
    assert masked.api_key_ref == "env-key"
    assert masked.base_url == "https://api.openai.com/v1"
    assert masked.is_default is True
    db.add.assert_not_called()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_openai_default_chat_model_skips_when_env_incomplete():
    db = AsyncMock()
    db.add = MagicMock()

    model = await ensure_openai_default_chat_model(db, openai_config(api_key=""))

    assert model is None
    db.execute.assert_not_called()
    db.add.assert_not_called()
