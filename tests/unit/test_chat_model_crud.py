"""Unit tests for chat model CRUD behavior."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from structure.core.constants.llm import MASKED_API_KEY_REF
from structure.models.llm.chat_model import ChatModel
from structure.schemas.llm.chat_model import (
    ChatModelCreate,
    ChatModelResponse,
    ChatModelUpdate,
)
from structure.services.llm.chat_model_crud import ChatModelCRUD
from tests.unit.routers.conftest import USER_ID


def make_chat_model(**overrides) -> ChatModel:
    defaults = {
        "id": uuid4(),
        "name": "System OpenAI Chat",
        "description": None,
        "user_id": None,
        "provider": "openai",
        "model_id": "gpt-4.1-mini",
        "base_url": "https://api.openai.com/v1",
        "api_key_ref": "real-key",
        "supports_vision": False,
        "supports_function_call": True,
        "supports_streaming": True,
        "currency": "USD",
        "is_system": True,
        "is_default": True,
        "enabled": True,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return ChatModel(**defaults)


@pytest.mark.asyncio
async def test_update_system_chat_model_keeps_key_when_masked_value_submitted():
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    obj = make_chat_model()
    crud = ChatModelCRUD(db)
    crud.get_by_id = AsyncMock(return_value=obj)

    updated = await crud.update(
        str(obj.id),
        ChatModelUpdate(api_key_ref=MASKED_API_KEY_REF, base_url="https://new"),
    )

    assert updated is obj
    assert obj.api_key_ref == "real-key"
    assert obj.base_url == "https://new"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(obj)


@pytest.mark.asyncio
async def test_create_default_chat_model_clears_previous_default():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    crud = ChatModelCRUD(db)

    model = await crud.create(
        ChatModelCreate(
            name="Custom",
            provider="openai",
            model_id="gpt-4.1-mini",
            base_url="https://api.openai.com/v1",
            api_key_ref="key",
            is_default=True,
        ),
        user_id=USER_ID,
        auto_commit=False,
    )

    assert model.is_default is True
    db.execute.assert_awaited_once()
    db.add.assert_called_once_with(model)
    db.flush.assert_awaited_once()


def test_chat_model_response_masks_system_api_key():
    response = ChatModelResponse.model_validate(make_chat_model())

    assert response.model_dump()["api_key_ref"] == MASKED_API_KEY_REF
