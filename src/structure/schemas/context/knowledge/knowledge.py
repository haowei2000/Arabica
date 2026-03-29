"""Pydantic schemas for Knowledge API endpoints."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from structure.utils.schema_mixins import ResponseMixin


class KnowledgeCreate(BaseModel):
    """Schema for creating a new knowledge base."""

    name: str = Field(
        ..., min_length=1, max_length=255, description="Knowledge base name"
    )
    description: str | None = Field(None, description="Knowledge base description")
    preprocess_id: str | UUID | None = Field(
        None, description="Preprocess configuration ID"
    )
    permission: str = Field(default="private", description="Permission setting")
    meta: dict[str, Any] | None = Field(None, description="Additional metadata")


class KnowledgeUpdate(BaseModel):
    """Schema for updating a knowledge base."""

    name: str | None = Field(None, min_length=1, max_length=255, description="Name")
    description: str | None = Field(None, description="Description")
    status: str | None = Field(None, description="Status")
    permission: str | None = Field(None, description="Permission setting")
    preprocess_id: str | UUID | None = Field(None, description="Preprocess config ID")
    meta: dict[str, Any] | None = Field(None, description="Additional metadata")


class KnowledgeResponse(ResponseMixin, BaseModel):
    """Schema for knowledge base response."""

    id: str
    name: str
    description: str | None = None
    user_id: str
    owner_id: str | None = None
    preprocess_id: str | None = None
    status: str
    permission: str
    meta: dict[str, Any] | None = None
    document_count: int
    chunk_count: int
    # created_at, updated_at, UUID conversion, ORM config inherited from ResponseMixin


class KnowledgeListResponse(BaseModel):
    """Schema for paginated list of knowledge bases."""

    total: int = Field(..., description="Total number of knowledge bases")
    items: list[KnowledgeResponse] = Field(..., description="List of knowledge bases")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")
