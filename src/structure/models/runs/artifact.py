"""Artifact model - stores agent-produced outputs within a run."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from structure.core.enums.runs import ArtifactType
from structure.extensions.database import get_base

if TYPE_CHECKING:
    from structure.models.runs.run import Run
    from structure.models.workspaces.workspace import Workspace

Base = get_base("structure")


class Artifact(Base):
    """Artifact model - a product or output generated during an agent run.

    Artifacts are versioned outputs (text, code, files, documents, etc.) produced
    by the agent loop. Each artifact belongs to a workspace and optionally a run.
    """

    __tablename__ = "artifact"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4, comment="Artifact unique ID"
    )

    # Workspace reference
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
        comment="Workspace ID",
    )

    # Run reference (optional — artifact may outlive the run)
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("run.id", ondelete="SET NULL"),
        nullable=True,
        comment="Run ID that produced this artifact",
    )

    # Name and type
    name: Mapped[str] = mapped_column(String(512), nullable=False, comment="Artifact name")
    artifact_type: Mapped[str] = mapped_column(
        String(50),
        default=ArtifactType.TEXT,
        comment="Artifact type: text/code/file/image/document/data/other",
    )
    content_type: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="MIME type (e.g., text/plain, application/json)"
    )

    # Content — either inline text or a reference to S3 storage
    content: Mapped[str | None] = mapped_column(Text, nullable=True, comment="Artifact content (inline)")
    s3_key: Mapped[str | None] = mapped_column(
        String(1024), nullable=True, comment="S3 storage key for large artifact files"
    )
    s3_url: Mapped[str | None] = mapped_column(
        String(2048), nullable=True, comment="S3 public/presigned URL for artifact access"
    )

    # Versioning
    version: Mapped[int] = mapped_column(Integer, default=1, comment="Artifact version number")

    # Flexible metadata
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="Additional metadata (tags, source, etc.)"
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        comment="Creation time",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        comment="Last update time",
    )

    # Relationships
    workspace: Mapped[Workspace] = relationship("Workspace", back_populates="artifacts")
    run: Mapped[Run | None] = relationship("Run", back_populates="artifacts")

    __table_args__ = (
        Index("ix_artifact_workspace", "workspace_id", "created_at"),
        Index("ix_artifact_run", "run_id", "created_at"),
        Index("ix_artifact_type", "artifact_type"),
    )

    def __repr__(self) -> str:
        return f"<Artifact(id={self.id}, name='{self.name}', type='{self.artifact_type}')>"
