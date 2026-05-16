"""Schemas for workspace chat-file uploads."""

from datetime import datetime

from pydantic import BaseModel, Field


class ChatFileAttachment(BaseModel):
    """Attachment reference stored on user.message event payloads."""

    id: str = Field(..., description="WorkspaceContext ID")
    name: str = Field(..., description="Original/display file name")
    path: str = Field(..., description="Structured context path")
    content_type: str | None = Field(None, description="MIME type")
    size_bytes: int | None = Field(None, description="File size in bytes")
    download_url: str | None = Field(None, description="Authenticated download URL")
    parse_status: str | None = Field(None, description="parsed/skipped/failed")


class ChatFileResponse(ChatFileAttachment):
    """Uploaded chat-file response."""

    created_at: datetime


class ChatFileUploadResponse(BaseModel):
    """Response for a multi-file chat upload."""

    total: int
    items: list[ChatFileResponse]
