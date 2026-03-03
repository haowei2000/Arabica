"""Pydantic schemas for Artifact API endpoints."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from aiwen.utils.schema_mixins import ResponseMixin


class ArtifactResponse(ResponseMixin, BaseModel):
    """Schema for artifact response."""

    id: str
    workspace_id: str
    run_id: str | None = None
    name: str
    artifact_type: str
    content_type: str | None = None
    content: str | None = None
    s3_key: str | None = None
    s3_url: str | None = None
    version: int
    meta: dict[str, Any] | None = None


class ArtifactListResponse(BaseModel):
    """Paginated artifact list response."""

    total: int
    items: list[ArtifactResponse]
