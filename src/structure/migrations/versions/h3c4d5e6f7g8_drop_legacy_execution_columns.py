"""Drop legacy execution mode columns from tool table.

All custom tools now use mapping-based delegation to InnerTools.
The http_config, code, container_config, client_config, and celery_config
columns are no longer needed.

Revision ID: h3c4d5e6f7g8
Revises: g2b3c4d5e6f7
Create Date: 2026-02-19

"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "h3c4d5e6f7g8"
down_revision: str | Sequence[str] | None = "g2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE tool DROP COLUMN IF EXISTS code"))
    conn.execute(sa.text("ALTER TABLE tool DROP COLUMN IF EXISTS http_config"))
    conn.execute(sa.text("ALTER TABLE tool DROP COLUMN IF EXISTS container_config"))
    conn.execute(sa.text("ALTER TABLE tool DROP COLUMN IF EXISTS client_config"))
    conn.execute(sa.text("ALTER TABLE tool DROP COLUMN IF EXISTS celery_config"))


def downgrade() -> None:
    op.add_column(
        "tool",
        sa.Column("celery_config", JSONB, nullable=True, comment="Celery configuration"),
    )
    op.add_column(
        "tool",
        sa.Column("client_config", JSONB, nullable=True, comment="Client configuration"),
    )
    op.add_column(
        "tool",
        sa.Column(
            "container_config", JSONB, nullable=True, comment="Container configuration"
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "http_config", JSONB, nullable=True, comment="HTTP configuration for http mode"
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "code", sa.Text, nullable=True, comment="Python code for server_run mode"
        ),
    )
