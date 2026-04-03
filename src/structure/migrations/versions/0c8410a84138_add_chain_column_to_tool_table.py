"""add_chain_column_to_tool_table

Revision ID: 0c8410a84138
Revises: h3c4d5e6f7g8
Create Date: 2026-02-26 09:05:04.880621

"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0c8410a84138"
down_revision: str | Sequence[str] | None = "405c96961f3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE tool ADD COLUMN IF NOT EXISTS chain JSONB"))
    conn.execute(sa.text("ALTER TABLE tool DROP COLUMN IF EXISTS execution_mode"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tool", "chain")
    op.add_column(
        "tool",
        sa.Column(
            "execution_mode",
            sa.VARCHAR(length=50),
            nullable=True,
            comment="Execution mode: server_run, http, client_run, container_run, celery_run",
        ),
    )
