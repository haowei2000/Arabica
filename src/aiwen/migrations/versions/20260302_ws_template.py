"""Replace workspace.app_id with workspace.agent_template_id.

Revision ID: 20260302_ws_template
Revises: 20260301_baseline
Create Date: 2026-03-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260302_ws_template"
down_revision = "20260301_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("workspace")}

    if "agent_template_id" not in columns:
        op.add_column(
            "workspace",
            sa.Column("agent_template_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_workspace_agent_template",
            "workspace",
            "agent_template",
            ["agent_template_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if "app_id" in columns:
        op.execute(
            """
            UPDATE workspace w
            SET agent_template_id = a.agent_template_id
            FROM app a
            WHERE w.app_id = a.id
            """
        )
        op.drop_column("workspace", "app_id")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("workspace")}

    if "app_id" not in columns:
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

    if "agent_template_id" in columns:
        op.drop_constraint("fk_workspace_agent_template", "workspace", type_="foreignkey")
        op.drop_column("workspace", "agent_template_id")
