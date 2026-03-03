"""Pydantic schemas for Task API endpoints."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from aiwen.utils.schema_mixins import ResponseMixin


class TaskResponse(ResponseMixin, BaseModel):
    """Schema for task response."""

    id: str
    workspace_id: str
    run_id: str | None = None
    parent_task_id: str | None = None
    title: str
    description: str | None = None
    result: str | None = None
    status: str
    priority: int
    assignee: str | None = None
    meta: dict[str, Any] | None = None
    completed_at: datetime | None = None


class TaskListResponse(BaseModel):
    """Paginated task list response."""

    total: int
    items: list[TaskResponse]
