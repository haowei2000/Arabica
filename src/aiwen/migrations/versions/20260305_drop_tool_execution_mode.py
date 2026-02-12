"""Drop execution_mode column from tool table.

All tools now run through the unified execute()/\__call__() protocol.
The execution_mode discriminator is no longer needed.

Revision ID: 20260305_drop_exec_mode
Revises: 20260304_ws_app_id
Create Date: 2026-03-05
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260305_drop_exec_mode"
down_revision = "20260304_ws_app_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("tool", "execution_mode")


def downgrade() -> None:
    op.add_column(
        "tool",
        sa.Column(
            "execution_mode",
            sa.String(50),
            nullable=True,
            comment="Execution mode: server_run, http, client_run, container_run, celery_run",
        ),
    )
