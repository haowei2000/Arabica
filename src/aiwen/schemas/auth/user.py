"""Pydantic models for user authentication and management."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class UserBase(BaseModel):
    """Base user model with common fields."""
    username: str = Field(..., min_length=3, max_length=50, description="用户名")
    email: EmailStr | None = Field(None, description="邮箱地址")
    phone: str | None = Field(None, description="手机号码")


class UserCreate(UserBase):
    """User creation model."""
    password: str = Field(..., min_length=8, description="密码")


class UserLogin(BaseModel):
    """User login model."""
    username: str = Field(..., description="用户名或邮箱")
    password: str = Field(..., description="密码")


class UserInDB(UserBase):
    """User model as stored in database."""
    id: UUID
    tenant_id: UUID
    role: str
    is_active: bool
    is_superuser: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserResponse(UserBase):
    """User response model."""
    id: UUID
    tenant_id: UUID
    role: str
    is_active: bool
    is_superuser: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
