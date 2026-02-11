from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ExecutorResponse(BaseModel):
    """Schema for executor response."""

    id: UUID = Field(..., description="Executor UUID")
    executor_code: str = Field(..., description="Unique executor code")
    executor_name: str = Field(..., description="Executor name")
    enabled: bool = Field(..., description="Whether the executor is enabled")
    config: dict[str, Any] | None = Field(None, description="Executor configuration")
    version: int = Field(..., description="Executor version")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime | None = Field(None, description="Last update timestamp")

    class Config:
        from_attributes = True
