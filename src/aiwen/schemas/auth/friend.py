"""Pydantic schemas for friend management."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class FriendRequestCreate(BaseModel):
    """Schema for sending a friend request."""

    friend_id: UUID


class FriendRequestAction(BaseModel):
    """Schema for accepting or declining a friend request."""

    pass


class FriendResponse(BaseModel):
    """Schema for a friend relationship record."""

    id: UUID
    user_id: UUID
    friend_id: UUID
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FriendUserInfo(BaseModel):
    """Schema for a friend's basic user info."""

    id: UUID
    username: str
    email: str | None = None
    friendship_id: UUID
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class FriendListResponse(BaseModel):
    """Paginated list of friends."""

    total: int
    items: list[FriendUserInfo]
    page: int
    page_size: int
