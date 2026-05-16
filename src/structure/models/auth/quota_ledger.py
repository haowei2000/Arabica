"""Immutable quota ledger entries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from structure.extensions.database import get_base

Base = get_base("structure")


class QuotaLedger(Base):
    """Append-only quota balance movement."""

    __tablename__ = "quota_ledger"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("auth_user.id", ondelete="CASCADE"),
        nullable=False,
        comment="User affected by this quota movement",
    )
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("run.id", ondelete="SET NULL"),
        nullable=True,
        comment="Run that consumed quota",
    )
    delta_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Positive for grants, negative for consumption",
    )
    balance_after: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Remaining tokens after this ledger entry",
    )
    reason: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="grant/consume/refund/admin_adjust reason",
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        comment="Provider/run details for auditing",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        Index("idx_quota_ledger_user_id_created_at", "user_id", "created_at"),
        Index("idx_quota_ledger_run_id", "run_id"),
    )
