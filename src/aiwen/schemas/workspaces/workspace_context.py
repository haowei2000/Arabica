"""Pydantic schemas for workspace context API endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from aiwen.types import ContextPath
from aiwen.utils.schema_mixins import ResponseMixin


class WorkspaceContextResponse(ResponseMixin, BaseModel):
    """Schema for workspace context response."""

    id: str
    workspace_id: str
    name: str
    path: str | None = None
    content_type: str | None = None
    s3_key: str | None = None
    size_bytes: int | None = None
    meta: dict[str, Any] | None = None


class WorkspaceContextListResponse(BaseModel):
    """Schema for paginated list of workspace contexts."""

    total: int = Field(..., description="Total number of workspace contexts")
    items: list[WorkspaceContextResponse] = Field(..., description="List of workspace contexts")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")


class CopyContextsRequest(BaseModel):
    """Schema for copying user contexts into a workspace."""

    context_ids: list[str] = Field(..., min_length=1, description="IDs of contexts to copy")
    path_prefix: ContextPath | None = Field(None, description="Optional path prefix for copied entries")


class CopyContextsResponse(BaseModel):
    """Schema for copy contexts result."""

    copied_count: int = Field(..., description="Number of contexts successfully copied")
    items: list[WorkspaceContextResponse] = Field(..., description="Newly created workspace contexts")
