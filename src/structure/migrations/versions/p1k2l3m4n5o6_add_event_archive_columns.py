"""add event archive columns

Revision ID: p1k2l3m4n5o6
Revises: o0j1k2l3m4n5
Create Date: 2026-05-10 12:30:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "p1k2l3m4n5o6"
down_revision: str | Sequence[str] | None = "o0j1k2l3m4n5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("event")}

    if "is_archived" not in existing_columns:
        op.add_column(
            "event",
            sa.Column(
                "is_archived",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
    if "archived_at" not in existing_columns:
        op.add_column(
            "event",
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "archive_scope" not in existing_columns:
        op.add_column(
            "event",
            sa.Column("archive_scope", sa.String(length=20), nullable=True),
        )
    if "archive_reason" not in existing_columns:
        op.add_column(
            "event",
            sa.Column("archive_reason", sa.String(length=255), nullable=True),
        )
    if "archive_context_id" not in existing_columns:
        op.add_column(
            "event",
            sa.Column("archive_context_id", sa.UUID(), nullable=True),
        )

    existing_fks = {
        fk["name"] for fk in inspector.get_foreign_keys("event") if fk.get("name")
    }
    if "fk_event_archive_context_id" not in existing_fks:
        op.create_foreign_key(
            "fk_event_archive_context_id",
            "event",
            "context",
            ["archive_context_id"],
            ["id"],
            ondelete="SET NULL",
        )

    existing_indexes = {ix["name"] for ix in inspector.get_indexes("event")}
    if "ix_event_archived" not in existing_indexes:
        op.create_index("ix_event_archived", "event", ["is_archived"])
    if "ix_event_run_archived_sequence" not in existing_indexes:
        op.create_index(
            "ix_event_run_archived_sequence",
            "event",
            ["run_id", "is_archived", "sequence"],
        )
    if "ix_event_workspace_archived_created" not in existing_indexes:
        op.create_index(
            "ix_event_workspace_archived_created",
            "event",
            ["workspace_id", "is_archived", "created_at"],
        )


def downgrade() -> None:
    op.drop_index("ix_event_workspace_archived_created", table_name="event")
    op.drop_index("ix_event_run_archived_sequence", table_name="event")
    op.drop_index("ix_event_archived", table_name="event")
    op.drop_constraint("fk_event_archive_context_id", "event", type_="foreignkey")
    op.drop_column("event", "archive_context_id")
    op.drop_column("event", "archive_reason")
    op.drop_column("event", "archive_scope")
    op.drop_column("event", "archived_at")
    op.drop_column("event", "is_archived")
