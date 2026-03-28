from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ContextMetadata(BaseModel):
    """Metadata for a context entry."""

    id: UUID = Field(default_factory=uuid4)
    workspace_id: UUID
    path: str
    name: str
    content_type: str | None = None
    glance: str | None = None
    summary: str | None = None
    size_bytes: int | None = None
    tags: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ContextCreateRequest(BaseModel):
    """Request to create a new context entry."""

    path: str
    name: str | None = None
    content: str | None = None
    content_type: str | None = None
    glance: str | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None


class ContextResponse(BaseModel):
    """Response containing context entry data."""

    path: str
    name: str
    content_type: str | None
    glance: str | None
    summary: str | None
    content: str | None = None
    size_bytes: int | None
    tags: list[str]
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ContextListResponse(BaseModel):
    """Response containing a list of context entries."""

    workspace_id: UUID
    total: int
    items: list[ContextResponse]
