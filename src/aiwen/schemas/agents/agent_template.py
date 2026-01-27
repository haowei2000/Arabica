from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AgentTemplateResponse(BaseModel):
    """Schema for agent template response."""

    id: UUID = Field(..., description="Agent template UUID")
    template_code: str = Field(..., description="Unique template code")
    template_name: str = Field(..., description="Template name")
    enabled: bool = Field(..., description="Whether the template is enabled")
    config: dict[str, Any] | None = Field(None, description="Agent configuration")
    version: int = Field(..., description="Template version")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime | None = Field(None, description="Last update timestamp")

    class Config:
        from_attributes = True
