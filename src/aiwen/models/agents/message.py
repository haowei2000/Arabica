"""Message model for storing conversation messages."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aiwen.extensions.database import get_base
from aiwen.models.types import LongText

Base = get_base("aiwen")


class Message(Base):
    """Message model for storing individual messages within conversations."""

    __tablename__ = "messages"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="message_pkey"),
        Index("message_app_id_idx", "app_id", "created_at"),
        Index("message_conversation_id_idx", "conversation_id"),
        Index("message_account_idx", "app_id", "from_source", "from_account_id"),
        Index("message_created_at_idx", "created_at"),
        Index("message_app_mode_idx", "app_mode"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    app_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=True)
    conversation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False
    )
    summary: Mapped[str | None] = mapped_column(LongText)
    query: Mapped[str] = mapped_column(LongText, nullable=False)
    message: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False)
    answer: Mapped[str] = mapped_column(LongText, nullable=False)
    status: Mapped[str] = mapped_column(String(255), nullable=False, server_default=sa.text("'normal'"))
    error: Mapped[str | None] = mapped_column(LongText)
    message_metadata: Mapped[str | None] = mapped_column(LongText)
    from_source: Mapped[str] = mapped_column(String(255), nullable=False)
    from_account_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True))
    from_end_user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True))
    workflow_run_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True))
    app_mode: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )
    conversation = relationship("Conversation", back_populates="messages")
