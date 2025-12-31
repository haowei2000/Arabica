from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aiwen.extensions.database import get_base
from aiwen.models.types import LongText

Base = get_base("aiwen")


class Conversation(Base):
    """Conversation model for tracking user conversations with agents."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    app_id = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    summary = mapped_column(LongText)
    status: Mapped[str] = mapped_column(String(255), nullable=False)

    from_source: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id = mapped_column(UUID(as_uuid=True))
    from_end_user_id = mapped_column(UUID(as_uuid=True))
    read_at = mapped_column(sa.DateTime(timezone=True))
    dialogue_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at = mapped_column(sa.DateTime(timezone=True), nullable=False, server_default=func.current_timestamp())
    updated_at = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    messages = relationship("Message", back_populates="conversation", lazy="select", passive_deletes=True)
    is_deleted: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.text("false"))
