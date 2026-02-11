"""Pydantic schemas for Document API endpoints."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from aiwen.utils.schema_mixins import ResponseMixin


class DocumentCreate(BaseModel):
    """Schema for creating a new document."""

    knowledge_id: str | UUID = Field(
        ..., description="ID of the knowledge base this document belongs to"
    )
    original_name: str = Field(
        ..., min_length=1, max_length=255, description="Original file name"
    )
    mime_type: str | None = Field(None, description="MIME type of the file")


class DocumentResponse(ResponseMixin, BaseModel):
    """Schema for document response."""

    id: str
    knowledge_id: str
    user_id: str
    original_name: str
    object_key: str
    file_url: str | None = None
    file_size: int
    mime_type: str | None = None
    storage_type: str
    bucket_name: str | None = None
    status: str = "pending"
    error_message: str | None = None
    chunk_count: int = 0
    # created_at, updated_at, UUID conversion, ORM config inherited from ResponseMixin


class DocumentUploadResponse(BaseModel):
    """Schema for document upload response with task tracking."""

    document: DocumentResponse = Field(..., description="Created document details")
    task_id: str = Field(..., description="Celery task ID for tracking processing")


class DocumentListResponse(BaseModel):
    """Schema for paginated list of documents."""

    total: int = Field(..., description="Total number of documents")
    items: list[DocumentResponse] = Field(..., description="List of documents")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")
