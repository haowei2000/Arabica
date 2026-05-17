"""add event batch tables

Revision ID: s4n5o6p7q8r9
Revises: r3m4n5o6p7q8
Create Date: 2026-05-17 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "s4n5o6p7q8r9"
down_revision: str | Sequence[str] | None = "r3m4n5o6p7q8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    if "event_batch" not in existing_tables:
        op.create_table(
            "event_batch",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("context_key", sa.String(length=512), nullable=False),
            sa.Column("context_kind", sa.String(length=64), nullable=False),
            sa.Column("sequence_start", sa.Integer(), nullable=False),
            sa.Column("sequence_end", sa.Integer(), nullable=False),
            sa.Column(
                "event_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "event_type_counts",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "input_tokens",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "output_tokens",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "load_state",
                sa.String(length=32),
                nullable=False,
                server_default="load_key",
            ),
            sa.Column(
                "state",
                sa.String(length=32),
                nullable=False,
                server_default="active",
            ),
            sa.Column(
                "load_epoch",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("key_content", sa.Text(), nullable=False, server_default=""),
            sa.Column("key_hash", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("summary_context_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("archive_context_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["archive_context_id"], ["context.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["run_id"], ["run.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["summary_context_id"], ["context.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workspace_id", "context_key", name="uq_event_batch_context"),
        )

    if "event_batch_item" not in existing_tables:
        op.create_table(
            "event_batch_item",
            sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["batch_id"], ["event_batch.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["event_id"], ["event.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("batch_id", "event_id"),
            sa.UniqueConstraint("event_id", name="uq_event_batch_item_event"),
        )

    existing_indexes = {
        table: {ix["name"] for ix in inspector.get_indexes(table)}
        for table in ("event_batch", "event_batch_item")
        if table in set(sa.inspect(conn).get_table_names())
    }
    if "ix_event_batch_workspace_epoch_state" not in existing_indexes.get(
        "event_batch",
        set(),
    ):
        op.create_index(
            "ix_event_batch_workspace_epoch_state",
            "event_batch",
            ["workspace_id", "load_epoch", "load_state"],
        )
    if "ix_event_batch_run_sequence_start" not in existing_indexes.get(
        "event_batch",
        set(),
    ):
        op.create_index(
            "ix_event_batch_run_sequence_start",
            "event_batch",
            ["run_id", "sequence_start"],
        )
    if "ix_event_batch_context_key" not in existing_indexes.get("event_batch", set()):
        op.create_index(
            "ix_event_batch_context_key",
            "event_batch",
            ["context_key"],
        )
    if "ix_event_batch_item_batch_sequence" not in existing_indexes.get(
        "event_batch_item",
        set(),
    ):
        op.create_index(
            "ix_event_batch_item_batch_sequence",
            "event_batch_item",
            ["batch_id", "sequence"],
        )


def downgrade() -> None:
    op.drop_index("ix_event_batch_item_batch_sequence", table_name="event_batch_item")
    op.drop_index("ix_event_batch_context_key", table_name="event_batch")
    op.drop_index("ix_event_batch_run_sequence_start", table_name="event_batch")
    op.drop_index("ix_event_batch_workspace_epoch_state", table_name="event_batch")
    op.drop_table("event_batch_item")
    op.drop_table("event_batch")
