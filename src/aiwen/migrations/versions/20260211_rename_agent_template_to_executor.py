"""Rename agent_template table to executor

Revision ID: 20260211_rename_agent_template
Revises: 20260211_drop_agent_task
Create Date: 2026-02-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260211_rename_agent_template"
down_revision = "20260211_drop_agent_task"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Rename agent_template table to executor and update columns."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Check if agent_template table exists
    tables = inspector.get_table_names()

    if "executor" in tables:
        # Already migrated, nothing to do
        pass
    elif "agent_template" in tables:
        # Rename agent_template table to executor
        op.rename_table("agent_template", "executor")

        # Rename columns in the executor table
        columns = {col["name"] for col in inspector.get_columns("executor")}

        if "template_code" in columns:
            op.alter_column("executor", "template_code", new_column_name="executor_code")
        if "template_name" in columns:
            op.alter_column("executor", "template_name", new_column_name="executor_name")
    else:
        # Neither table exists, create executor table from scratch
        op.create_table(
            "executor",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("executor_code", sa.String(), unique=True, nullable=False),
            sa.Column("executor_name", sa.String(), nullable=False),
            sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("config", postgresql.JSONB(), nullable=True),
            sa.Column("version", sa.Integer(), server_default="1", nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    # Update app table - rename agent_template_id to executor_id
    if "app" in tables:
        app_columns = {col["name"] for col in inspector.get_columns("app")}
        if "agent_template_id" in app_columns:
            op.alter_column("app", "agent_template_id", new_column_name="executor_id")


def downgrade() -> None:
    """Revert executor table to agent_template."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = inspector.get_table_names()

    # Revert app table changes
    if "app" in tables:
        app_columns = {col["name"] for col in inspector.get_columns("app")}
        if "executor_id" in app_columns:
            op.alter_column("app", "executor_id", new_column_name="agent_template_id")

    # Revert executor table column names
    if "executor" in tables:
        columns = {col["name"] for col in inspector.get_columns("executor")}

        if "executor_code" in columns:
            op.alter_column("executor", "executor_code", new_column_name="template_code")
        if "executor_name" in columns:
            op.alter_column("executor", "executor_name", new_column_name="template_name")

        # Rename executor table back to agent_template
        op.rename_table("executor", "agent_template")
