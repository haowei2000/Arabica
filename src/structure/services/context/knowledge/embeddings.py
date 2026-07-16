"""Embedding service for generating vector embeddings."""

import logging
from typing import Literal

from langchain_openai import OpenAIEmbeddings

logger = logging.getLogger(__name__)

EmbeddingProvider = Literal["openai", "custom"]

# Common embedding models and their dimensions
EMBEDDING_MODELS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

# Valid embedding dimensions for our database schema
VALID_DIMENSIONS = [384, 768, 1024, 1536]


class EmbeddingService:
    """Service for generating text embeddings using various providers."""

    def __init__(
        self,
        provider: EmbeddingProvider = "openai",
        model: str = "text-embedding-3-small",
        dimension: int = 1536,
    ) -> None:
        self.provider = provider
        self.model = model
        self.dimension = dimension
        self._client = self._create_client()

        logger.info(
            f"Initialized EmbeddingService: provider={provider}, "
            f"model={model}, dimension={dimension}"
        )

    def _create_client(self) -> OpenAIEmbeddings:
        """Create an OpenAI-compatible embedding client."""
        from structure.config.factory import get_settings

        if self.provider not in ("openai", "custom"):
            raise ValueError(f"Unsupported embedding provider: {self.provider}")
        openai_settings = get_settings().openai
        api_key = (openai_settings.api_key if openai_settings else "").strip()
        base_url = (openai_settings.base_url if openai_settings else "").strip()
        model = (openai_settings.model if openai_settings else "").strip()
        if not api_key or not base_url or not model:
            raise ValueError(
                "No embedding model configured. Seed the default embedding model from "
                "OPENAI__API_KEY, OPENAI__BASE_URL, and OPENAI__MODEL."
            )
        return OpenAIEmbeddings(
            model=self.model,
            openai_api_key=api_key,
            openai_api_base=base_url,
            dimensions=self.dimension,
        )

    def embed_text(self, text: str) -> list[float]:
        """Generate embedding for a single text.

        Args:
            text: Text to embed.

        Returns:
            list[float]: Embedding vector.
        """
        text = self._sanitize_text(text)
        return self._client.embed_query(text)

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """Sanitize text for embedding API.

        Removes null bytes, control characters, and ensures proper encoding.

        Args:
            text: Input text.

        Returns:
            str: Sanitized text.
        """
        if not isinstance(text, str):
            text = str(text)
        # Remove null bytes and control characters (except newlines and tabs)
        text = text.replace("\x00", "")
        text = "".join(
            char
            for char in text
            if char == "\n" or char == "\t" or not (0 <= ord(char) < 32)
        )
        # Normalize whitespace
        text = " ".join(text.split())
        return text  # noqa: RET504

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed.

        Returns:
            list[list[float]]: List of embedding vectors.
        """
        if not texts:
            return []

        # Validate and sanitize texts - ensure all are non-empty strings
        valid_texts = []
        for i, text in enumerate(texts):
            if text is None:
                raise ValueError(f"Text at index {i} is None")
            if not isinstance(text, str):
                logger.warning(
                    f"Text at index {i} is not a string (type={type(text)}), converting"
                )
                text = str(text)
            # Sanitize text to remove problematic characters
            text = self._sanitize_text(text)
            if not text.strip():
                raise ValueError(f"Text at index {i} is empty after sanitization")
            valid_texts.append(text)

        if not valid_texts:
            logger.warning("No valid texts to embed")
            return []

        # Log debug info for troubleshooting
        logger.info(f"Embedding {len(valid_texts)} texts")
        for i, text in enumerate(valid_texts[:3]):  # Log first 3 for debugging
            logger.debug(
                f"Text {i}: type={type(text).__name__}, len={len(text)}, preview={text[:50]!r}..."
            )

        try:
            embeddings = self._client.embed_documents(valid_texts)
            logger.info(f"Generated {len(embeddings)} embeddings")
            return embeddings
        except Exception as e:
            # Log the problematic texts on error
            logger.error(f"Embedding error: {e}")
            logger.error(
                f"Texts info: count={len(valid_texts)}, types={[type(t).__name__ for t in valid_texts[:5]]}"
            )
            for i, text in enumerate(valid_texts[:3]):
                logger.error(f"Text {i}: {text[:100]!r}")
            raise

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
            batch = texts[i : i + batch_size]
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
            model: ChatLLM name.

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
    provider: EmbeddingProvider = "openai",
    model: str = "text-embedding-3-small",
    dimension: int | None = None,
) -> EmbeddingService:
    """Factory function to create embedding service.

    Args:
        provider: Embedding provider.
        model: ChatLLM name.
        dimension: Optional dimension override. If None, uses model default.

    Returns:
        EmbeddingService: Configured embedding service.
    """
    if dimension is None:
        dimension = EMBEDDING_MODELS.get(model, 1536)

    return EmbeddingService(provider=provider, model=model, dimension=dimension)
