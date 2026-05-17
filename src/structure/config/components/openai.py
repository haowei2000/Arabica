# config/components/openai.py

from pydantic import BaseModel, Field


class OpenAIConfig(BaseModel):
    """OpenAI API configuration for Tongyi and other OpenAI-compatible providers."""

    api_key: str = Field(default="", description="OpenAI API key")
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="OpenAI API base URL",
    )
    model: str = Field(default="gpt-4.1-mini", description="Default chat model name")
    embedding_model: str = Field(
        default="text-embedding-3-small",
        description="Default embedding model name",
    )
    embedding_dimension: int = Field(
        default=1536,
        gt=0,
        description="Default embedding vector dimension",
    )
