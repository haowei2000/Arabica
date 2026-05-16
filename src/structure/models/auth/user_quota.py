"""User quota models for free and paid token allowances."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from structure.extensions.database import get_base

if TYPE_CHECKING:
    from structure.models.auth.user import User

Base = get_base("structure")


class UserQuota(Base):
    """Current quota counters for a user."""

    __tablename__ = "user_quota"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("auth_user.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="User that owns this quota",
    )
    free_quota_total: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="Granted free tokens"
    )
    free_quota_used: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="Consumed free tokens"
    )
    paid_quota_total: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="Purchased tokens"
    )
    paid_quota_used: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="Consumed paid tokens"
    )
    period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Quota period start"
    )
    period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Quota period end"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    user: Mapped[User] = relationship("User")

    __table_args__ = (Index("idx_user_quota_user_id", "user_id"),)

    @property
    def free_remaining(self) -> int:
        """Remaining free tokens, clamped to zero."""
        return max(self.free_quota_total - self.free_quota_used, 0)

    @property
    def paid_remaining(self) -> int:
        """Remaining paid tokens, clamped to zero."""
        return max(self.paid_quota_total - self.paid_quota_used, 0)

    @property
    def remaining_tokens(self) -> int:
        """Total remaining tokens, clamped to zero."""
        return self.free_remaining + self.paid_remaining
