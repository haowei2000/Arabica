"""Pydantic schemas for ToolBundle API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ToolBundleCreate(BaseModel):
    name: str
    description: str | None = None
    tags: list[str] = []
    is_public: bool = False
    tool_ids: list[UUID] = []


class ToolBundleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    is_public: bool | None = None
    tool_ids: list[UUID] | None = None


class ToolBundleResponse(BaseModel):
    id: UUID
    bundle_type: str
    source: str | None
    user_id: UUID | None
    name: str
    description: str | None
    tags: list[str] | None
    is_public: bool
    tool_ids: list[UUID]
    tool_count: int
    created_at: datetime
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class ToolBundleListResponse(BaseModel):
    bundles: list[ToolBundleResponse]
    total: int
