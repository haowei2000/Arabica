"""Skill schemas for API requests and responses."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class SkillBase(BaseModel):
    """Base skill schema."""

    name: str = Field(..., min_length=1, max_length=200, description="Skill name")
    description: str | None = Field(None, description="Skill description")
    content: str = Field(..., min_length=1, description="Skill content in Markdown format")
    tags: list[str] | None = Field(None, description="Skill tags for categorization")


class SkillCreate(SkillBase):
    """Schema for creating a new skill."""

    workspace_id: UUID | None = Field(None, description="Associated workspace ID")
    source_id: UUID | None = Field(None, description="Source ID if derived from another resource")
    path: str | None = Field(None, description="Virtual path for organization")
    meta: dict[str, Any] | None = Field(None, description="Additional metadata")


class SkillUpdate(BaseModel):
    """Schema for updating an existing skill."""

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    content: str | None = Field(None, min_length=1)
    tags: list[str] | None = None
    meta: dict[str, Any] | None = None


class SkillResponse(SkillBase):
    """Schema for skill response."""

    id: UUID
    user_id: UUID
    source_id: UUID | None
    workspace_id: UUID | None = None
    path: str | None
    glance: str | None = Field(None, description="One-line summary")
    summary: str | None = Field(None, description="Structured summary")
    has_embedding: bool = Field(False, description="Whether embeddings have been generated")
    created_at: datetime
    updated_at: datetime | None

    class Config:
        from_attributes = True


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
