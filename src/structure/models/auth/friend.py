"""Friend model for social features."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from structure.extensions.database import get_base

Base = get_base("structure")


class Friend(Base):
    """Friend model - represents a friend relationship between two users.

    Tracks friend requests and their acceptance status.
    """

    __tablename__ = "auth_friend"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="Friend record unique ID",
    )

    # The user who sent the friend request
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        comment="User who initiated the friend request",
    )

    # The user who received the friend request
    friend_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        comment="User who received the friend request",
    )

    # Request status
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
        comment="Friendship status: pending/accepted/declined/blocked",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        comment="Creation time",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        comment="Update time",
    )

    __table_args__ = (
        # Each pair of users can only have one friendship record
        UniqueConstraint("user_id", "friend_id", name="uq_auth_friend_pair"),
        Index("ix_auth_friend_user", "user_id"),
        Index("ix_auth_friend_friend", "friend_id"),
        Index("ix_auth_friend_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<Friend(id={self.id}, user_id={self.user_id}, friend_id={self.friend_id}, status={self.status!r})>"
