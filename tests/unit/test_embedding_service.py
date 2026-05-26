import pytest

from structure.services.context.knowledge import embeddings


def test_embedding_service_uses_openai_compatible_config(monkeypatch):
    captured: dict[str, object] = {}

    class FakeOpenAIEmbeddings:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(embeddings, "OpenAIEmbeddings", FakeOpenAIEmbeddings)
    monkeypatch.setenv("OPENAI__API_KEY", "test-key")
    monkeypatch.setenv("OPENAI__BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI__MODEL", "test-model")

    from structure.config.factory import get_settings

    get_settings.cache_clear()

    embeddings.EmbeddingService(
        provider="openai",
        model="text-embedding-3-small",
        dimension=1536,
    )

    assert captured == {
        "model": "text-embedding-3-small",
        "openai_api_key": "test-key",
        "openai_api_base": "https://api.openai.com/v1",
        "dimensions": 1536,
    }
    get_settings.cache_clear()


def test_embedding_service_requires_openai_env_seeded_config(monkeypatch):
    monkeypatch.delenv("OPENAI__API_KEY", raising=False)
    monkeypatch.delenv("OPENAI__BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI__MODEL", raising=False)

    from structure.config.factory import get_settings

    get_settings.cache_clear()

    with pytest.raises(ValueError, match="OPENAI__API_KEY"):
        embeddings.EmbeddingService(
            provider="openai",
            model="text-embedding-3-small",
            dimension=1536,
        )
    get_settings.cache_clear()
