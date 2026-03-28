"""add scope column to context table

Revision ID: j5e6f7g8h9i0
Revises: e57d623197a6
Create Date: 2026-03-22 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j5e6f7g8h9i0"
down_revision: Union[str, None] = "e57d623197a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE context ADD COLUMN IF NOT EXISTS scope VARCHAR(20) DEFAULT 'user' NOT NULL"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_context_scope ON context (scope)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_context_user_scope ON context (user_id, scope)"))


def downgrade() -> None:
    op.drop_index("ix_context_user_scope", table_name="context")
    op.drop_index("ix_context_scope", table_name="context")
    op.drop_column("context", "scope")
