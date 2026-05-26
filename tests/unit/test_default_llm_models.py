"""Tests for platform default LLM model initialization."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError
import pytest

from structure.core.bootstrap import ApplicationBootstrap, BootstrapConfig
from structure.models.llm.chat_model import ChatModel
from structure.models.llm.embedding_model import EmbeddingModel
from structure.schemas.llm.chat_model import (
    ChatModelCreate,
    ChatModelResponse,
    ChatModelUpdate,
)
from structure.schemas.llm.embedding_model import (
    EmbeddingModelCreate,
    EmbeddingModelResponse,
    EmbeddingModelUpdate,
)


def _empty_scalar_result():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    return result


@pytest.mark.asyncio
async def test_seed_default_llm_models_creates_system_models_from_openai_settings():
    session = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            _empty_scalar_result(),
            MagicMock(),
            _empty_scalar_result(),
            MagicMock(),
        ]
    )
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    @asynccontextmanager
    async def fake_get_session(bind_name: str):
        assert bind_name == "structure"
        yield session

    bootstrap = ApplicationBootstrap(BootstrapConfig(init_database=False))
    bootstrap.settings = SimpleNamespace(
        openai=SimpleNamespace(
            api_key="sk-platform",
            base_url="https://api.openai.com/v1",
            model="gpt-4.1-mini",
        )
    )

    with patch("structure.core.bootstrap.get_session", fake_get_session):
        await bootstrap._seed_default_llm_models()

    added_models = [call.args[0] for call in session.add.call_args_list]
    chat_model = next(model for model in added_models if isinstance(model, ChatModel))
    embedding_model = next(
        model for model in added_models if isinstance(model, EmbeddingModel)
    )

    assert chat_model.is_system is True
    assert chat_model.is_default is True
    assert chat_model.provider == "openai"
    assert chat_model.model_id == "gpt-4.1-mini"
    assert not hasattr(chat_model, "base_url")
    assert not hasattr(chat_model, "api_key_ref")

    assert embedding_model.is_system is True
    assert embedding_model.is_default is True
    assert embedding_model.model_id == "text-embedding-3-small"
    assert embedding_model.dimension == 1536
    assert not hasattr(embedding_model, "base_url")
    assert not hasattr(embedding_model, "api_key_ref")
    session.commit.assert_awaited_once()


def test_system_chat_model_response_masks_api_key():
    now = datetime.now(UTC)

    response = ChatModelResponse(
        id="00000000-0000-0000-0000-000000000001",
        name="Default OpenAI",
        user_id=None,
        provider="openai",
        model_id="gpt-4.1-mini",
        supports_vision=False,
        supports_function_call=True,
        supports_streaming=True,
        is_system=True,
        is_default=True,
        enabled=True,
        currency="USD",
        created_at=now,
        updated_at=now,
    )

    assert not hasattr(response, "api_key_ref")
    assert not hasattr(response, "base_url")


def test_user_chat_model_response_omits_api_config():
    now = datetime.now(UTC)

    response = ChatModelResponse(
        id="00000000-0000-0000-0000-000000000001",
        name="My OpenAI",
        user_id="00000000-0000-0000-0000-000000000002",
        provider="openai",
        model_id="gpt-4.1-mini",
        supports_vision=False,
        supports_function_call=True,
        supports_streaming=True,
        is_system=False,
        is_default=False,
        enabled=True,
        currency="USD",
        created_at=now,
        updated_at=now,
    )

    assert not hasattr(response, "api_key_ref")
    assert not hasattr(response, "base_url")


def test_chat_model_create_rejects_runtime_api_config():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ChatModelCreate(
            name="Custom",
            provider="openai",
            model_id="ignored",
            base_url="http://custom.example/v1",
            api_key_ref="custom-key",
        )


def test_embedding_model_create_rejects_runtime_api_config():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EmbeddingModelCreate(
            name="Custom Embeddings",
            provider="openai",
            model_id="text-embedding-3-small",
            base_url="http://custom.example/v1",
            api_key_ref="custom-key",
            dimension=1536,
        )


def test_model_schemas_reject_runtime_api_config_inside_config_maps():
    with pytest.raises(ValidationError, match="OPENAI__ env vars"):
        ChatModelUpdate(config={"base_url": "http://custom.example/v1"})

    with pytest.raises(ValidationError, match="OPENAI__ env vars"):
        EmbeddingModelUpdate(meta={"api_key": "custom-key"})


def test_system_embedding_model_response_masks_api_key():
    now = datetime.now(UTC)

    response = EmbeddingModelResponse(
        id="00000000-0000-0000-0000-000000000001",
        name="Default OpenAI Embeddings",
        user_id=None,
        provider="openai",
        model_id="text-embedding-3-small",
        dimension=1536,
        supports_batch=True,
        batch_size=32,
        normalize=True,
        distance_metric="cosine",
        currency="USD",
        is_system=True,
        is_default=True,
        enabled=True,
        created_at=now,
        updated_at=now,
    )

    assert not hasattr(response, "api_key_ref")
    assert not hasattr(response, "base_url")
