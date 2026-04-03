"""Add executor_code and executor_config to workspace table.

Workspaces now carry their own executor configuration, replacing the
mandatory App lookup on every run.  Existing runs that reference an App
continue to work via the worker's 3-level fallback:
  1. run.app_id  -> App.config   (legacy, unchanged)
  2. workspace.executor_config   (new native config)
  3. executor template defaults  (always present)

Revision ID: i4d5e6f7g8h9
Revises: d1e2f3a4b5c6
Create Date: 2026-03-02

"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "i4d5e6f7g8h9"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "ALTER TABLE workspace ADD COLUMN IF NOT EXISTS executor_code VARCHAR(255)"
        )
    )
    conn.execute(
        sa.text("ALTER TABLE workspace ADD COLUMN IF NOT EXISTS executor_config JSONB")
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE workspace DROP COLUMN IF EXISTS executor_config"))
    conn.execute(sa.text("ALTER TABLE workspace DROP COLUMN IF EXISTS executor_code"))
