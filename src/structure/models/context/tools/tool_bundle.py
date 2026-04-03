"""ORM models for ToolBundle and ToolBundleItem."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from structure.extensions.database import get_base

Base = get_base("structure")


class ToolBundle(Base):
    __tablename__ = "tool_bundle"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    bundle_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="user",
        server_default="user",
        comment="Bundle type: inner, mcp, user",
    )
    source: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Source identifier: folder/category for inner, server URL/command for mcp",
    )

    user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
        comment="Owner user ID (NULL for inner bundles)",
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    is_public: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=True,
    )

    items: Mapped[list[ToolBundleItem]] = relationship(
        "ToolBundleItem",
        back_populates="bundle",
        cascade="all, delete-orphan",
        lazy="select",
    )


class ToolBundleItem(Base):
    __tablename__ = "tool_bundle_item"

    bundle_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tool_bundle.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tool_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tool.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    bundle: Mapped[ToolBundle] = relationship("ToolBundle", back_populates="items")
