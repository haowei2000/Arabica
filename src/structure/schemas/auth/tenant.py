"""Pydantic models for tenant management."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TenantBase(BaseModel):
    """Base tenant model with common fields."""

    name: str = Field(..., min_length=1, max_length=100, description="租户名称")
    description: str | None = Field(None, max_length=500, description="租户描述")


class TenantCreate(TenantBase):
    """Tenant creation model."""

    pass


class TenantInDB(TenantBase):
    """Tenant model as stored in database."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TenantResponse(TenantBase):
    """Tenant response model."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    is_active: bool
    created_at: datetime
