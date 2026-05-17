"""Context batch models for event memory loading."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PGUUID,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from structure.core.enums.events import ContextBatchLoadState, ContextBatchState
from structure.extensions.database import get_base

if TYPE_CHECKING:
    from structure.models.context.context import Context
    from structure.models.events.event import Event
    from structure.models.runs.run import Run
    from structure.models.workspaces.workspace import Workspace

Base = get_base("structure")


class EventBatch(Base):
    """A deterministic context-memory unit built from related event rows."""

    __tablename__ = "event_batch"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("run.id", ondelete="CASCADE"),
        nullable=True,
    )
    context_key: Mapped[str] = mapped_column(String(512), nullable=False)
    context_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence_start: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_end: Mapped[int] = mapped_column(Integer, nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_type_counts: Mapped[dict[str, int]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    load_state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ContextBatchLoadState.LOAD_KEY.value,
        server_default=ContextBatchLoadState.LOAD_KEY.value,
    )
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ContextBatchState.ACTIVE.value,
        server_default=ContextBatchState.ACTIVE.value,
    )
    load_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    key_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    summary_context_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("context.id", ondelete="SET NULL"),
        nullable=True,
    )
    archive_context_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("context.id", ondelete="SET NULL"),
        nullable=True,
    )
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    workspace: Mapped[Workspace] = relationship("Workspace")
    run: Mapped[Run | None] = relationship("Run")
    items: Mapped[list[EventBatchItem]] = relationship(
        "EventBatchItem",
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="EventBatchItem.sequence",
    )
    summary_context: Mapped[Context | None] = relationship(
        "Context",
        foreign_keys=[summary_context_id],
    )
    archive_context: Mapped[Context | None] = relationship(
        "Context",
        foreign_keys=[archive_context_id],
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "context_key", name="uq_event_batch_context"),
        Index(
            "ix_event_batch_workspace_epoch_state",
            "workspace_id",
            "load_epoch",
            "load_state",
        ),
        Index("ix_event_batch_run_sequence_start", "run_id", "sequence_start"),
        Index("ix_event_batch_context_key", "context_key"),
    )


class EventBatchItem(Base):
    """Mapping from event rows to their context batch."""

    __tablename__ = "event_batch_item"

    batch_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("event_batch.id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("event.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    batch: Mapped[EventBatch] = relationship("EventBatch", back_populates="items")
    event: Mapped[Event] = relationship("Event")

    __table_args__ = (
        UniqueConstraint("event_id", name="uq_event_batch_item_event"),
        Index("ix_event_batch_item_batch_sequence", "batch_id", "sequence"),
    )
