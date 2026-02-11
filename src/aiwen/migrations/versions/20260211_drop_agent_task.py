"""Drop agent_task table

Revision ID: 20260211_drop_agent_task
Revises: a1b2c3d4e5f6
Create Date: 2026-02-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260211_drop_agent_task"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop the agent_task table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Check if table exists before dropping
    tables = inspector.get_table_names()
    if "agent_task" in tables:
        op.drop_table("agent_task")


def downgrade() -> None:
    """Recreate the agent_task table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = inspector.get_table_names()
    if "agent_task" not in tables:
        op.create_table(
            "agent_task",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("app_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("task_type", sa.String(), nullable=True),
            sa.Column("status", sa.String(), default="pending"),
            sa.Column("payload", postgresql.JSONB(), nullable=True),
            sa.Column("result", postgresql.JSONB(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("progress", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                default=sa.func.now(),
                onupdate=sa.func.now(),
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
