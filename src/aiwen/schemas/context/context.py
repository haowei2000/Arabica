"""Pydantic schemas for Context API endpoints."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from aiwen.schemas.agents.app import ContextType


class ContextCreate(BaseModel):
    """Schema for creating a new context entry."""

    context_type: ContextType = Field(
        default=ContextType.CONVERSATION,
        description="Context type: conversation, message, skill, tool, chunk",
    )
    source_id: str | UUID | None = Field(
        None, description="Related source ID (e.g., knowledge_id, conversation_id)"
    )
    content: str = Field(..., min_length=1, description="Context content")
    summary: str | None = Field(None, description="Context summary")
    keywords: list[str] | None = Field(None, description="Keywords for search")
    embedding_384: list[float] | None = Field(None, description="384-dim embedding")
    embedding_768: list[float] | None = Field(None, description="768-dim embedding")
    embedding_1024: list[float] | None = Field(None, description="1024-dim embedding")
    embedding_1536: list[float] | None = Field(None, description="1536-dim embedding")
    meta: dict[str, Any] | None = Field(None, description="Additional metadata")
    importance: int = Field(
        default=0, ge=0, le=100, description="Importance score 0-100"
    )


class ContextUpdate(BaseModel):
    """Schema for updating a context entry."""

    context_type: ContextType | None = Field(None, description="Context type")
    source_id: str | UUID | None = Field(None, description="Related source ID")
    content: str | None = Field(None, min_length=1, description="Context content")
    summary: str | None = Field(None, description="Context summary")
    keywords: list[str] | None = Field(None, description="Keywords for search")
    embedding_384: list[float] | None = Field(None, description="384-dim embedding")
    embedding_768: list[float] | None = Field(None, description="768-dim embedding")
    embedding_1024: list[float] | None = Field(None, description="1024-dim embedding")
    embedding_1536: list[float] | None = Field(None, description="1536-dim embedding")
    meta: dict[str, Any] | None = Field(None, description="Additional metadata")
    importance: int | None = Field(None, ge=0, le=100, description="Importance score")


class ContextResponse(BaseModel):
    """Schema for context response."""

    id: str
    user_id: str
    source_id: str | None = None
    context_type: str
    content: str
    summary: str | None = None
    keywords: list[str] | None = None
    meta: dict[str, Any] | None = None
    importance: int | None = None
    created_at: datetime
    updated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def convert_uuids(cls, data: Any) -> Any:
        """Convert UUIDs to strings."""
        if hasattr(data, "__dict__"):
            result = {}
            for field_name in cls.model_fields.keys():
                value = getattr(data, field_name, None)
                if isinstance(value, UUID):
                    result[field_name] = str(value)
                else:
                    result[field_name] = value
            return result
        return data

    class Config:
        from_attributes = True


class ContextWithScore(ContextResponse):
    """Context response with similarity score for vector search."""

    score: float = Field(..., description="Similarity score (cosine distance)")


class ContextListResponse(BaseModel):
    """Schema for paginated list of contexts."""

    total: int = Field(..., description="Total number of contexts")
    items: list[ContextResponse] = Field(..., description="List of contexts")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")


class ContextSearchResponse(BaseModel):
    """Schema for vector search results."""

    total: int = Field(..., description="Total number of results")
    items: list[ContextWithScore] = Field(
        ..., description="List of contexts with scores"
    )


class VectorSearchRequest(BaseModel):
    """Schema for vector similarity search request."""

    embedding: list[float] = Field(..., description="Query embedding vector")
    dimension: Literal[384, 768, 1024, 1536] = Field(
        default=1536, description="Embedding dimension to search"
    )
    context_type: ContextType | None = Field(None, description="Filter by context type")
    source_id: str | UUID | None = Field(None, description="Filter by source ID")
    top_k: int = Field(
        default=10, ge=1, le=100, description="Number of results to return"
    )
    threshold: float | None = Field(
        None, ge=0.0, le=1.0, description="Minimum similarity threshold"
    )


class GrepSearchRequest(BaseModel):
    """Schema for text grep search request."""

    query: str = Field(..., min_length=1, description="Search query string")
    context_type: ContextType | None = Field(None, description="Filter by context type")
    source_id: str | UUID | None = Field(None, description="Filter by source ID")
    search_in: list[Literal["content", "summary", "keywords"]] = Field(
        default=["content", "summary"], description="Fields to search in"
    )
    case_sensitive: bool = Field(default=False, description="Case sensitive search")
    skip: int = Field(default=0, ge=0, description="Number of records to skip")
    limit: int = Field(
        default=20, ge=1, le=100, description="Maximum results to return"
    )
