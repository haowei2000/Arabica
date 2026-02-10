"""create_workspace_run_event

Create tables for event-sourced architecture: workspace, workspace_member, run, event

Revision ID: e5f6a7b8c9d0
Revises: d44c36b57007
Create Date: 2026-01-27 10:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d44c36b57007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create workspace, workspace_member, run, and event tables."""

    # Create workspace table
    op.create_table(
        "workspace",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("app_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "visibility", sa.String(50), server_default="private", nullable=False
        ),
        sa.Column("is_shared", sa.Boolean, server_default="false", nullable=False),
        sa.Column("settings", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.String(50), server_default="active", nullable=False),
        sa.Column("run_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("member_count", sa.Integer, server_default="1", nullable=False),
        sa.Column(
            "legacy_conversation_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean, server_default="false", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["app_id"], ["app.id"], name="fk_workspace_app", ondelete="SET NULL"
        ),
    )

    # Create workspace indexes
    op.create_index("ix_workspace_owner", "workspace", ["owner_id", "created_at"])
    op.create_index("ix_workspace_visibility", "workspace", ["visibility", "status"])
    op.create_index(
        "ix_workspace_legacy_conversation", "workspace", ["legacy_conversation_id"]
    )
    op.create_index("ix_workspace_status", "workspace", ["status", "is_deleted"])

    # Create workspace_member table
    op.create_table(
        "workspace_member",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(50), server_default="viewer", nullable=False),
        sa.Column("invited_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "invitation_status",
            sa.String(50),
            server_default="accepted",
            nullable=False,
        ),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name="fk_workspace_member_workspace",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
    )

    # Create workspace_member indexes
    op.create_index("ix_workspace_member_user", "workspace_member", ["user_id"])
    op.create_index(
        "ix_workspace_member_workspace", "workspace_member", ["workspace_id", "role"]
    )
    op.create_index(
        "ix_workspace_member_invitation", "workspace_member", ["invitation_status"]
    )

    # Create run table
    op.create_table(
        "run",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("app_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(50), server_default="pending", nullable=False),
        sa.Column("trigger_type", sa.String(50), server_default="user", nullable=False),
        sa.Column("input_data", postgresql.JSONB, nullable=True),
        sa.Column("output_data", postgresql.JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("waiting_for", postgresql.JSONB, nullable=True),
        sa.Column(
            "last_event_sequence", sa.Integer, server_default="0", nullable=False
        ),
        sa.Column("legacy_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name="fk_run_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["app_id"], ["app.id"], name="fk_run_app", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id"], ["run.id"], name="fk_run_parent", ondelete="SET NULL"
        ),
    )

    # Create run indexes
    op.create_index("ix_run_workspace", "run", ["workspace_id", "created_at"])
    op.create_index("ix_run_status", "run", ["status", "created_at"])
    op.create_index("ix_run_user", "run", ["user_id", "created_at"])
    op.create_index("ix_run_app", "run", ["app_id", "created_at"])
    op.create_index("ix_run_parent", "run", ["parent_run_id"])
    op.create_index("ix_run_legacy_task", "run", ["legacy_task_id"])

    # Create event table
    op.create_table(
        "event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column("sequence", sa.Integer, server_default="0", nullable=False),
        sa.Column("parent_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name="fk_event_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["run.id"], name="fk_event_run", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_event_id"],
            ["event.id"],
            name="fk_event_parent",
            ondelete="SET NULL",
        ),
    )

    # Create event indexes
    op.create_index(
        "ix_event_workspace_sequence", "event", ["workspace_id", "sequence"]
    )
    op.create_index("ix_event_run_sequence", "event", ["run_id", "sequence"])
    op.create_index("ix_event_type_created", "event", ["event_type", "created_at"])
    op.create_index("ix_event_parent", "event", ["parent_event_id"])


def downgrade() -> None:
    """Drop workspace, workspace_member, run, and event tables."""

    # Drop event table and indexes
    op.drop_index("ix_event_parent", table_name="event")
    op.drop_index("ix_event_type_created", table_name="event")
    op.drop_index("ix_event_run_sequence", table_name="event")
    op.drop_index("ix_event_workspace_sequence", table_name="event")
    op.drop_table("event")

    # Drop run table and indexes
    op.drop_index("ix_run_legacy_task", table_name="run")
    op.drop_index("ix_run_parent", table_name="run")
    op.drop_index("ix_run_app", table_name="run")
    op.drop_index("ix_run_user", table_name="run")
    op.drop_index("ix_run_status", table_name="run")
    op.drop_index("ix_run_workspace", table_name="run")
    op.drop_table("run")

    # Drop workspace_member table and indexes
    op.drop_index("ix_workspace_member_invitation", table_name="workspace_member")
    op.drop_index("ix_workspace_member_workspace", table_name="workspace_member")
    op.drop_index("ix_workspace_member_user", table_name="workspace_member")
    op.drop_table("workspace_member")

    # Drop workspace table and indexes
    op.drop_index("ix_workspace_status", table_name="workspace")
    op.drop_index("ix_workspace_legacy_conversation", table_name="workspace")
    op.drop_index("ix_workspace_visibility", table_name="workspace")
    op.drop_index("ix_workspace_owner", table_name="workspace")
    op.drop_table("workspace")
