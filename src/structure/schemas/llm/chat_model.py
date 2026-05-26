"""Pydantic schemas for ChatModel API endpoints."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from structure.utils.schema_mixins import ResponseMixin

RUNTIME_API_CONFIG_KEYS = {
    "api_key",
    "api_key_ref",
    "base_url",
    "model",
    "openai_api_base",
    "openai_api_key",
}


def _assert_no_runtime_api_config(data: dict[str, Any] | None) -> None:
    if not data:
        return
    blocked = RUNTIME_API_CONFIG_KEYS.intersection(data)
    if blocked:
        keys = ", ".join(sorted(blocked))
        raise ValueError(
            f"Runtime LLM API config is only allowed through OPENAI__ env vars: {keys}"
        )


class ChatModelCreate(BaseModel):
    """Schema for creating a new chat model configuration."""

    model_config = ConfigDict(extra="forbid")

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
        description="Model identifier, e.g. gpt-4.1-mini",
    )
    max_tokens: int | None = Field(None, gt=0, description="Max output tokens")
    context_window: int | None = Field(None, gt=0, description="Context window size")
    supports_vision: bool = Field(False, description="Supports vision/image input")
    supports_function_call: bool = Field(
        True, description="Supports function/tool calling"
    )
    supports_streaming: bool = Field(True, description="Supports streaming output")
    default_temperature: float | None = Field(
        None, ge=0.0, le=2.0, description="Default temperature"
    )
    default_top_p: float | None = Field(
        None, ge=0.0, le=1.0, description="Default top_p"
    )
    default_max_tokens: int | None = Field(None, gt=0, description="Default max tokens")
    input_price: float | None = Field(
        None, ge=0, description="Input price per 1K tokens"
    )
    output_price: float | None = Field(
        None, ge=0, description="Output price per 1K tokens"
    )
    currency: str = Field("USD", max_length=10, description="Currency code")
    is_default: bool = Field(False, description="Set as default model")
    enabled: bool = Field(True, description="Whether model is enabled")
    config: dict[str, Any] | None = Field(None, description="Extra configuration")
    meta: dict[str, Any] | None = Field(None, description="Metadata")

    @model_validator(mode="after")
    def reject_runtime_api_config(self) -> "ChatModelCreate":
        _assert_no_runtime_api_config(self.config)
        _assert_no_runtime_api_config(self.meta)
        return self


class ChatModelUpdate(BaseModel):
    """Schema for updating an existing chat model configuration."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    provider: str | None = Field(None, min_length=1, max_length=50)
    model_id: str | None = Field(None, min_length=1, max_length=255)
    max_tokens: int | None = Field(None, gt=0)
    context_window: int | None = Field(None, gt=0)
    supports_vision: bool | None = None
    supports_function_call: bool | None = None
    supports_streaming: bool | None = None
    default_temperature: float | None = Field(None, ge=0.0, le=2.0)
    default_top_p: float | None = Field(None, ge=0.0, le=1.0)
    default_max_tokens: int | None = Field(None, gt=0)
    input_price: float | None = Field(None, ge=0)
    output_price: float | None = Field(None, ge=0)
    currency: str | None = Field(None, max_length=10)
    is_default: bool | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None

    @model_validator(mode="after")
    def reject_runtime_api_config(self) -> "ChatModelUpdate":
        _assert_no_runtime_api_config(self.config)
        _assert_no_runtime_api_config(self.meta)
        return self


class ChatModelResponse(ResponseMixin, BaseModel):
    """Schema for chat model response."""

    id: str
    name: str
    description: str | None = None
    user_id: str | None = None
    provider: str
    model_id: str
    max_tokens: int | None = None
    context_window: int | None = None
    supports_vision: bool
    supports_function_call: bool
    supports_streaming: bool
    default_temperature: float | None = None
    default_top_p: float | None = None
    default_max_tokens: int | None = None
    input_price: float | None = None
    output_price: float | None = None
    currency: str
    is_system: bool
    is_default: bool
    enabled: bool
    config: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None


class ChatModelListResponse(BaseModel):
    """Schema for paginated list of chat models."""

    total: int
    items: list[ChatModelResponse]
    page: int
    page_size: int
