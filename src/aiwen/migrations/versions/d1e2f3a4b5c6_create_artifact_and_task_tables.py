"""create artifact and task tables

Revision ID: d1e2f3a4b5c6
Revises: b52725032039
Create Date: 2026-02-28 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "b52725032039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create artifact and task tables."""
    # --- artifact table ---
    op.create_table(
        "artifact",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            comment="Artifact unique ID",
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            nullable=False,
            comment="Workspace ID",
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run.id", ondelete="SET NULL"),
            nullable=True,
            comment="Run ID that produced this artifact",
        ),
        sa.Column("name", sa.String(512), nullable=False, comment="Artifact name"),
        sa.Column(
            "artifact_type",
            sa.String(50),
            nullable=False,
            server_default="text",
            comment="Artifact type: text/code/file/image/document/data/other",
        ),
        sa.Column(
            "content_type",
            sa.String(255),
            nullable=True,
            comment="MIME type (e.g., text/plain, application/json)",
        ),
        sa.Column("content", sa.Text, nullable=True, comment="Artifact content (inline)"),
        sa.Column(
            "s3_key",
            sa.String(1024),
            nullable=True,
            comment="S3 storage key for large artifact files",
        ),
        sa.Column(
            "s3_url",
            sa.String(2048),
            nullable=True,
            comment="S3 public/presigned URL for artifact access",
        ),
        sa.Column(
            "version",
            sa.Integer,
            nullable=False,
            server_default="1",
            comment="Artifact version number",
        ),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Additional metadata (tags, source, etc.)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Creation time",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
            comment="Last update time",
        ),
    )
    op.create_index("ix_artifact_workspace", "artifact", ["workspace_id", "created_at"])
    op.create_index("ix_artifact_run", "artifact", ["run_id", "created_at"])
    op.create_index("ix_artifact_type", "artifact", ["artifact_type"])

    # --- task table ---
    op.create_table(
        "task",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            comment="Task unique ID",
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            nullable=False,
            comment="Workspace ID",
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("run.id", ondelete="SET NULL"),
            nullable=True,
            comment="Run ID this task belongs to",
        ),
        sa.Column(
            "parent_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("task.id", ondelete="SET NULL"),
            nullable=True,
            comment="Parent task ID for nested tasks",
        ),
        sa.Column("title", sa.String(512), nullable=False, comment="Task title"),
        sa.Column(
            "description", sa.Text, nullable=True, comment="Detailed task description"
        ),
        sa.Column(
            "result",
            sa.Text,
            nullable=True,
            comment="Task result or output after completion",
        ),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="pending",
            comment="Status: pending/in_progress/done/failed/cancelled",
        ),
        sa.Column(
            "priority",
            sa.Integer,
            nullable=False,
            server_default="2",
            comment="Priority level (1-5, higher = more urgent)",
        ),
        sa.Column(
            "assignee",
            sa.String(255),
            nullable=True,
            comment="Who this task is assigned to",
        ),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Additional metadata",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Creation time",
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Completion time",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
            comment="Last update time",
        ),
    )
    op.create_index("ix_task_workspace", "task", ["workspace_id", "created_at"])
    op.create_index("ix_task_run", "task", ["run_id", "status"])
    op.create_index("ix_task_status", "task", ["status", "priority"])
    op.create_index("ix_task_parent", "task", ["parent_task_id"])


def downgrade() -> None:
    """Drop artifact and task tables."""
    op.drop_index("ix_task_parent", table_name="task")
    op.drop_index("ix_task_status", table_name="task")
    op.drop_index("ix_task_run", table_name="task")
    op.drop_index("ix_task_workspace", table_name="task")
    op.drop_table("task")

    op.drop_index("ix_artifact_type", table_name="artifact")
    op.drop_index("ix_artifact_run", table_name="artifact")
    op.drop_index("ix_artifact_workspace", table_name="artifact")
    op.drop_table("artifact")
