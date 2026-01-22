"""Embedding service for generating vector embeddings."""

import logging
from typing import Literal

from langchain_ollama import OllamaEmbeddings
from langchain_openai import OpenAIEmbeddings

from aiwen.config.factory import get_settings

logger = logging.getLogger(__name__)

EmbeddingProvider = Literal["tongyi", "openai", "ollama"]

# Common embedding models and their dimensions
EMBEDDING_MODELS = {
    # DashScope/Tongyi models
    "text-embedding-v3": 1536,
    "text-embedding-v2": 1536,
    "text-embedding-v1": 1536,
    # OpenAI models
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    # Ollama models (dimensions vary)
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "bge-small": 384,
    "bge-base": 768,
    "bge-large": 1024,
}

# Valid embedding dimensions for our database schema
VALID_DIMENSIONS = [384, 768, 1024, 1536]


class EmbeddingService:
    """Service for generating text embeddings using various providers."""

    def __init__(
            self,
            provider: EmbeddingProvider = "tongyi",
            model: str = "text-embedding-v3",
            dimension: int = 1536,
    ) -> None:
        """Initialize embedding service.

        Args:
            provider: Embedding provider (tongyi, openai, ollama).
            model: Model name.
            dimension: Embedding dimension.
        """
        self.provider = provider
        self.model = model
        self.dimension = dimension
        self._client = self._create_client()

        logger.info(
            f"Initialized EmbeddingService: provider={provider}, "
            f"model={model}, dimension={dimension}"
        )

    def _create_client(self) -> OpenAIEmbeddings | OllamaEmbeddings:
        """Create embedding client based on provider.

        Returns:
            Embedding client instance.
        """
        settings = get_settings()

        match self.provider:
            case "tongyi":
                if settings.openai:
                    api_key = settings.openai.api_key or settings.dashscope_api_key
                    base_url = settings.openai.base_url
                else:
                    api_key = settings.dashscope_api_key
                    base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

                return OpenAIEmbeddings(
                    model=self.model,
                    openai_api_key=api_key,
                    openai_api_base=base_url,
                )

            case "openai":
                if not settings.openai:
                    raise ValueError("OpenAI configuration is not set")

                return OpenAIEmbeddings(
                    model=self.model,
                    openai_api_key=settings.openai.api_key,
                    openai_api_base=settings.openai.base_url,
                )

            case "ollama":
                if not settings.ollama:
                    raise ValueError("Ollama configuration is not set")

                return OllamaEmbeddings(
                    model=self.model,
                    base_url=settings.ollama.base_url,
                )

            case _:
                raise ValueError(f"Unsupported embedding provider: {self.provider}")

    def embed_text(self, text: str) -> list[float]:
        """Generate embedding for a single text.

        Args:
            text: Text to embed.

        Returns:
            list[float]: Embedding vector.
        """
        embedding = self._client.embed_query(text)
        return embedding

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed.

        Returns:
            list[list[float]]: List of embedding vectors.
        """
        if not texts:
            return []

        embeddings = self._client.embed_documents(texts)
        logger.info(f"Generated {len(embeddings)} embeddings")
        return embeddings

    def embed_texts_batch(
            self, texts: list[str], batch_size: int = 100
    ) -> list[list[float]]:
        """Generate embeddings in batches to avoid API limits.

        Args:
            texts: List of texts to embed.
            batch_size: Number of texts per batch.

        Returns:
            list[list[float]]: List of embedding vectors.
        """
        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i: i + batch_size]
            batch_embeddings = self.embed_texts(batch)
            all_embeddings.extend(batch_embeddings)
            logger.info(
                f"Processed embedding batch {i // batch_size + 1}/"
                f"{(len(texts) + batch_size - 1) // batch_size}"
            )

        return all_embeddings

    def get_embedding_field_name(self) -> str:
        """Get the database field name for this embedding dimension.

        Returns:
            str: Field name like 'embedding_768'.

        Raises:
            ValueError: If dimension is not supported.
        """
        if self.dimension not in VALID_DIMENSIONS:
            raise ValueError(
                f"Unsupported embedding dimension: {self.dimension}. "
                f"Valid dimensions: {VALID_DIMENSIONS}"
            )
        return f"embedding_{self.dimension}"

    @classmethod
    def get_model_dimension(cls, model: str) -> int | None:
        """Get the default dimension for a known model.

        Args:
            model: Model name.

        Returns:
            int | None: Dimension if known, None otherwise.
        """
        return EMBEDDING_MODELS.get(model)

    @classmethod
    def is_valid_dimension(cls, dimension: int) -> bool:
        """Check if dimension is valid for database storage.

        Args:
            dimension: Embedding dimension.

        Returns:
            bool: True if dimension is valid.
        """
        return dimension in VALID_DIMENSIONS


def get_embedding_service(
        provider: EmbeddingProvider = "tongyi",
        model: str = "text-embedding-v3",
        dimension: int | None = None,
) -> EmbeddingService:
    """Factory function to create embedding service.

    Args:
        provider: Embedding provider.
        model: Model name.
        dimension: Optional dimension override. If None, uses model default.

    Returns:
        EmbeddingService: Configured embedding service.
    """
    if dimension is None:
        dimension = EMBEDDING_MODELS.get(model, 1536)

    return EmbeddingService(provider=provider, model=model, dimension=dimension)
