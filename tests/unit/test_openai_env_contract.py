"""Tests for the single OpenAI-compatible LLM environment contract."""

from pydantic import ValidationError
import pytest

from structure.config.factory import get_settings
from structure.extensions.llm import llm as llm_module
from structure.schemas.app.app import AppCreate, AppUpdate


@pytest.fixture(autouse=True)
def clear_llm_settings_cache():
    get_settings.cache_clear()
    llm_module._llm_cache.clear()
    yield
    get_settings.cache_clear()
    llm_module._llm_cache.clear()


def test_get_llm_uses_openai_env_contract(monkeypatch):
    captured: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm_module, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setenv("OPENAI__API_KEY", "env-key")
    monkeypatch.setenv("OPENAI__BASE_URL", "http://env.example/v1")
    monkeypatch.setenv("OPENAI__MODEL", "env-model")

    result = llm_module.get_llm("legacy-model", provider="custom")

    assert isinstance(result, FakeChatOpenAI)
    assert captured["model"] == "env-model"
    assert captured["api_key"] == "env-key"
    assert captured["base_url"] == "http://env.example/v1"


def test_get_llm_requires_all_three_openai_env_values(monkeypatch):
    monkeypatch.delenv("OPENAI__API_KEY", raising=False)
    monkeypatch.delenv("OPENAI__BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI__MODEL", raising=False)

    with pytest.raises(ValueError, match="OPENAI__API_KEY"):
        llm_module.get_llm("ignored")


def test_get_llm_ignores_legacy_openai_env_aliases(monkeypatch):
    monkeypatch.delenv("OPENAI__API_KEY", raising=False)
    monkeypatch.delenv("OPENAI__BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI__MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://legacy.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "legacy-model")

    with pytest.raises(ValueError, match="OPENAI__API_KEY"):
        llm_module.get_llm("ignored")


def test_app_config_rejects_per_app_llm_api_config():
    with pytest.raises(ValidationError, match=r"config.model_provider"):
        AppCreate(
            app_code="custom-agent",
            config={
                "model_provider": "tongyi",
                "model_name": "qwen-plus",
                "approval_tools": [],
            },
        )

    with pytest.raises(ValidationError, match=r"config.llm.base_url"):
        AppUpdate(config={"llm": {"base_url": "http://custom.example/v1"}})
