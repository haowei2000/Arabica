"""
User Tool Model

Stores user-defined custom tools in the database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from aiwen.extensions.database import get_base

Base = get_base("aiwen")


class UserTool(Base):
    """User-defined custom tool"""

    __tablename__ = "user_tools"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Owner information
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, comment="Tool owner ID"
    )
    workspace_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, comment="Workspace ID (optional)"
    )

    # Tool identification
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Tool name (unique per user)"
    )
    display_name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="Display name"
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Tool description"
    )

    # Tool definition
    execution_mode: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="server_run",
        comment="Execution mode: server_run, http, client_run, container_run, celery_run",
    )

    # Input/Output schemas (JSON Schema format)
    input_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, comment="Input parameters schema (Pydantic format)"
    )
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="Output schema (optional)"
    )

    # Tool implementation
    code: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Python code for server_run mode"
    )
    http_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="HTTP configuration for http mode"
    )
    container_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="Container configuration for container_run mode"
    )
    client_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="Client configuration for client_run mode"
    )
    celery_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="Celery configuration for celery_run mode"
    )

    # Metadata
    category: Mapped[str] = mapped_column(
        String(50), default="custom", comment="Tool category"
    )
    tags: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True, comment="Tool tags"
    )
    version: Mapped[str] = mapped_column(
        String(20), default="1.0.0", comment="Tool version"
    )
    timeout: Mapped[int] = mapped_column(
        Integer, default=30, comment="Execution timeout in seconds"
    )

    # Status
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="Whether tool is enabled"
    )
    is_public: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="Whether tool is public (shared with others)"
    )
    verified: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="Whether tool is verified by admin"
    )

    # Usage statistics
    usage_count: Mapped[int] = mapped_column(
        Integer, default=0, comment="Number of times tool has been used"
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Last used timestamp"
    )

    # Audit fields
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), comment="Created at"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        comment="Updated at",
    )

    # Indexes
    __table_args__ = (
        Index("ix_user_tools_user_id", "user_id"),
        Index("ix_user_tools_workspace_id", "workspace_id"),
        Index("ix_user_tools_name", "name"),
        Index("ix_user_tools_enabled", "enabled"),
        Index("ix_user_tools_is_public", "is_public"),
        # Unique constraint: name is unique per user
        Index("ix_user_tools_user_name", "user_id", "name", unique=True),
    )

    def __repr__(self) -> str:
        return f"<UserTool(id={self.id}, name='{self.name}', owner={self.user_id})>"
