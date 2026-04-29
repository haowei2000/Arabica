"""Skill schemas for API requests and responses."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SkillBase(BaseModel):
    """Base skill schema (metadata only — content lives in the Context table)."""

    name: str = Field(..., min_length=1, max_length=200, description="Skill name")
    description: str | None = Field(None, description="Skill description")
    tags: list[str] | None = Field(None, description="Skill tags for categorization")


class SkillCreate(SkillBase):
    """Schema for creating a new skill."""

    content: str = Field(
        ...,
        min_length=1,
        description="Skill content in Markdown format (stored in Context table)",
    )
    meta: dict[str, Any] | None = Field(None, description="Additional metadata")


class SkillUpdate(BaseModel):
    """Schema for updating an existing skill."""

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    content: str | None = Field(
        None,
        min_length=1,
        description="New content; if omitted, existing content is unchanged",
    )
    tags: list[str] | None = None
    meta: dict[str, Any] | None = None


class SkillResponse(SkillBase):
    """Schema for skill response (metadata only)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    has_embedding: bool = Field(
        False, description="Whether embeddings have been generated"
    )
    files: dict[str, Any] | None = Field(
        None,
        description="Supplementary files metadata as {path: {s3_key, size, etag, content_type}}",
    )
    created_at: datetime
    updated_at: datetime | None


class SkillListResponse(BaseModel):
    """Paginated list of skills."""

    total: int = Field(..., description="Total number of skills")
    items: list[SkillResponse] = Field(..., description="List of skills")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")


class SkillProcessRequest(BaseModel):
    """Request to process/reprocess a skill's embeddings."""

    skill_id: UUID
    embedding_model: str | None = Field(None, description="Embedding model to use")
