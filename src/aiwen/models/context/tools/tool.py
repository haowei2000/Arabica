"""Unified Tool model.

Stores definitions of all tools — both inner (code-defined, synced on startup)
and external (user-defined, delegating to InnerTools). The `tool_type` column
discriminates between the two:
  - "inner": built-in tools synced from code at startup (user_id=NULL)
  - "external": user-created tools that delegate to an InnerTool backend
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from aiwen.extensions.database import get_base

Base = get_base("aiwen")


class Tool(Base):
    __tablename__ = "tool"

    # Primary key
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Tool identification
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="Tool name")
    tool_code: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, comment="Unique tool identifier"
    )
    description: Mapped[str | None] = mapped_column(Text, comment="Tool description")
    display_name: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="Display name"
    )

    # Tool type: "inner" (code-defined) or "external" (user-defined)
    tool_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="external",
        server_default="external",
        comment="Tool type: inner, external",
    )

    # InnerTool delegation
    inner_tool_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Name of the InnerTool to delegate to",
    )
    parameter_mapping: Mapped[dict[str, str] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Maps external param names to InnerTool param names",
    )

    # Schema and configuration
    input_schema: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        comment="Input parameters JSON Schema",
    )
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Output schema (optional)",
    )
    config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        comment="Tool execution configuration",
    )

    # Tool implementation
    code: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Python code for server_run mode"
    )
    http_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="HTTP configuration for http mode"
    )
    container_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="Container configuration"
    )
    client_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="Client configuration"
    )
    celery_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="Celery configuration"
    )

    # Ownership
    user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        comment="Creator user ID (NULL for inner tools)",
    )
    workspace_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
        comment="Workspace ID (optional)",
    )

    # Metadata
    category: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="Tool category"
    )
    tags: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True, comment="Tool tags"
    )

    # Status
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        comment="Whether tool is enabled",
    )
    is_public: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        comment="Whether tool is publicly available",
    )
    verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        comment="Whether tool is verified by admin",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
        comment="Version number",
    )
    timeout: Mapped[int] = mapped_column(
        Integer,
        default=30,
        server_default="30",
        comment="Execution timeout in seconds",
    )

    # Usage statistics
    usage_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        comment="Number of times tool has been used",
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Last used timestamp"
    )

    # Audit fields (timezone-aware)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        comment="Created at",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        comment="Updated at",
    )

    def __repr__(self) -> str:
        return (
            f"<Tool(id={self.id}, name='{self.name}', "
            f"tool_code='{self.tool_code}', tool_type='{self.tool_type}', "
            f"enabled={self.enabled})>"
        )
