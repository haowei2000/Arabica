"""Pydantic schemas for EmbeddingModel API endpoints."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from structure.utils.schema_mixins import ResponseMixin


class EmbeddingModelCreate(BaseModel):
    """Schema for creating a new embedding model configuration."""

    name: str = Field(..., min_length=1, max_length=255, description="Display name")
    description: str | None = Field(None, description="Model description")
    provider: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Provider: openai or custom OpenAI-compatible endpoint",
    )
    model_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Model identifier, e.g. text-embedding-3-small",
    )
    base_url: str | None = Field(None, max_length=500, description="API base URL")
    api_key_ref: str | None = Field(
        None, max_length=255, description="API key reference name"
    )
    dimension: int = Field(..., gt=0, description="Vector dimension: 384/768/1024/1536")
    max_tokens: int | None = Field(None, gt=0, description="Max input tokens")
    supports_batch: bool = Field(True, description="Supports batch processing")
    batch_size: int = Field(32, gt=0, description="Default batch size")
    normalize: bool = Field(True, description="Normalize output vectors")
    distance_metric: str = Field(
        "cosine",
        max_length=20,
        description="Distance metric: cosine/euclidean/dot_product",
    )
    price: float | None = Field(None, ge=0, description="Price per 1K tokens")
    currency: str = Field("USD", max_length=10, description="Currency code")
    is_default: bool = Field(False, description="Set as default model")
    enabled: bool = Field(True, description="Whether model is enabled")
    config: dict[str, Any] | None = Field(None, description="Extra configuration")
    meta: dict[str, Any] | None = Field(None, description="Metadata")

    @model_validator(mode="after")
    def reject_runtime_api_config(self) -> "EmbeddingModelCreate":
        if self.base_url or self.api_key_ref:
            raise ValueError(
                "LLM API configuration is process-level. Use OPENAI__API_KEY, "
                "OPENAI__BASE_URL, and OPENAI__MODEL."
            )
        return self


class EmbeddingModelUpdate(BaseModel):
    """Schema for updating an existing embedding model configuration."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    provider: str | None = Field(None, min_length=1, max_length=50)
    model_id: str | None = Field(None, min_length=1, max_length=255)
    base_url: str | None = Field(None, max_length=500)
    api_key_ref: str | None = Field(None, max_length=255)
    dimension: int | None = Field(None, gt=0)
    max_tokens: int | None = Field(None, gt=0)
    supports_batch: bool | None = None
    batch_size: int | None = Field(None, gt=0)
    normalize: bool | None = None
    distance_metric: str | None = Field(None, max_length=20)
    price: float | None = Field(None, ge=0)
    currency: str | None = Field(None, max_length=10)
    is_default: bool | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None

    @model_validator(mode="after")
    def reject_runtime_api_config(self) -> "EmbeddingModelUpdate":
        if self.base_url or self.api_key_ref:
            raise ValueError(
                "LLM API configuration is process-level. Use OPENAI__API_KEY, "
                "OPENAI__BASE_URL, and OPENAI__MODEL."
            )
        return self


class EmbeddingModelResponse(ResponseMixin, BaseModel):
    """Schema for embedding model response."""

    id: str
    name: str
    description: str | None = None
    user_id: str | None = None
    provider: str
    model_id: str
    base_url: str | None = None
    api_key_ref: str | None = None
    dimension: int
    max_tokens: int | None = None
    supports_batch: bool
    batch_size: int
    normalize: bool
    distance_metric: str
    price: float | None = None
    currency: str
    is_system: bool
    is_default: bool
    enabled: bool
    config: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None

    @model_validator(mode="after")
    def hide_system_api_key(self) -> "EmbeddingModelResponse":
        if self.is_system and self.api_key_ref:
            self.api_key_ref = "configured"
        return self


class EmbeddingModelListResponse(BaseModel):
    """Schema for paginated list of embedding models."""

    total: int
    items: list[EmbeddingModelResponse]
    page: int
    page_size: int
