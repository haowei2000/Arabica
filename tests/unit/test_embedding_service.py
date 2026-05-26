import pytest

from structure.services.context.knowledge import embeddings


def test_embedding_service_uses_openai_compatible_config(monkeypatch):
    captured: dict[str, object] = {}

    class FakeOpenAIEmbeddings:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(embeddings, "OpenAIEmbeddings", FakeOpenAIEmbeddings)

    embeddings.EmbeddingService(
        provider="openai",
        model="text-embedding-3-small",
        dimension=1536,
        api_key="test-key",
        base_url="https://api.openai.com/v1",
    )

    assert captured == {
        "model": "text-embedding-3-small",
        "openai_api_key": "test-key",
        "openai_api_base": "https://api.openai.com/v1",
        "dimensions": 1536,
    }


def test_embedding_service_requires_openai_env_seeded_config():
    with pytest.raises(ValueError, match="OPENAI__API_KEY"):
        embeddings.EmbeddingService(
            provider="openai",
            model="text-embedding-3-small",
            dimension=1536,
            api_key="",
            base_url="",
        )
