from structure.services.context.knowledge.embeddings import EmbeddingService


def test_dashscope_base_url_uses_direct_embedding_path():
    service = EmbeddingService(
        provider="openai",
        model="text-embedding-v4",
        dimension=1024,
        api_key="test-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    calls = []

    def fake_direct(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[0.1] * 1024 for _ in texts]

    service._embed_tongyi_direct = fake_direct

    vectors = service.embed_texts(["hello"])

    assert calls == [["hello"]]
    assert len(vectors) == 1
    assert len(vectors[0]) == 1024
