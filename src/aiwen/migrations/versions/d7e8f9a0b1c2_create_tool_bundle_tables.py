"""create tool bundle tables

Revision ID: d7e8f9a0b1c2
Revises: b52725032039
Create Date: 2026-03-11 10:30:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "d7e8f9a0b1c2"
down_revision: str | Sequence[str] | None = "i4d5e6f7g8h9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS tool_bundle (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL,
            name VARCHAR(100) NOT NULL,
            description TEXT,
            tags JSONB,
            is_public BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ
        )
    """))
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS tool_bundle_item (
            bundle_id UUID NOT NULL REFERENCES tool_bundle(id) ON DELETE CASCADE,
            tool_id UUID NOT NULL REFERENCES tool(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (bundle_id, tool_id)
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_tool_bundle_user_id ON tool_bundle (user_id)"))
    conn.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS ux_tool_bundle_user_name ON tool_bundle (user_id, name)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_tool_bundle_item_bundle_id ON tool_bundle_item (bundle_id)"))


def downgrade() -> None:
    op.drop_index("ix_tool_bundle_item_bundle_id", table_name="tool_bundle_item")
    op.drop_table("tool_bundle_item")
    op.drop_index("ux_tool_bundle_user_name", table_name="tool_bundle")
    op.drop_index("ix_tool_bundle_user_id", table_name="tool_bundle")
    op.drop_table("tool_bundle")
