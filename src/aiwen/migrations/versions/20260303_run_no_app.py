"""Remove app_id from run table.

Revision ID: 20260303_run_no_app
Revises: 20260302_ws_template
Create Date: 2026-03-03
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260303_run_no_app"
down_revision = "20260302_ws_template"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("run")}

    if "app_id" in columns:
        # Use CASCADE to drop dependent constraints/indexes if any exist.
        op.execute('ALTER TABLE "run" DROP COLUMN IF EXISTS app_id CASCADE')


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("run")}

    if "app_id" not in columns:
        op.add_column("run", sa.Column("app_id", sa.UUID(), nullable=True))
        op.create_foreign_key(
            "fk_run_app",
            "run",
            "app",
            ["app_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index("ix_run_app", "run", ["app_id", "created_at"])
