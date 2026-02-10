"""Replace workspace.agent_template_id with workspace.app_id and add app_id to run.

Revision ID: 20260304_ws_app_id
Revises: 20260303_run_no_app
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260304_ws_app_id"
down_revision = "20260303_run_no_app"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Update workspace table
    ws_columns = {col["name"] for col in inspector.get_columns("workspace")}

    if "app_id" not in ws_columns:
        op.add_column(
            "workspace",
            sa.Column("app_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_workspace_app",
            "workspace",
            "app",
            ["app_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if "agent_template_id" in ws_columns:
        op.drop_constraint(
            "fk_workspace_agent_template", "workspace", type_="foreignkey"
        )
        op.drop_column("workspace", "agent_template_id")

    # Update run table - add app_id
    run_columns = {col["name"] for col in inspector.get_columns("run")}

    if "app_id" not in run_columns:
        # Add as nullable first for existing data
        op.add_column(
            "run",
            sa.Column("app_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_run_app",
            "run",
            "app",
            ["app_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index("ix_run_app", "run", ["app_id", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Downgrade run table
    run_columns = {col["name"] for col in inspector.get_columns("run")}

    if "app_id" in run_columns:
        op.drop_index("ix_run_app", table_name="run")
        op.drop_constraint("fk_run_app", "run", type_="foreignkey")
        op.drop_column("run", "app_id")

    # Downgrade workspace table
    ws_columns = {col["name"] for col in inspector.get_columns("workspace")}

    if "agent_template_id" not in ws_columns:
        op.add_column(
            "workspace",
            sa.Column(
                "agent_template_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
        )
        op.create_foreign_key(
            "fk_workspace_agent_template",
            "workspace",
            "agent_template",
            ["agent_template_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if "app_id" in ws_columns:
        op.drop_constraint("fk_workspace_app", "workspace", type_="foreignkey")
        op.drop_column("workspace", "app_id")
