"""Pydantic models for authentication and token management."""

from uuid import UUID

from pydantic import BaseModel


class Token(BaseModel):
    """Token response model."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """Token data model."""

    user_id: str | None = None
    role: str | None = None
    tenant_id: UUID | None = None


class TokenRefresh(BaseModel):
    """Token refresh request model."""

    refresh_token: str
