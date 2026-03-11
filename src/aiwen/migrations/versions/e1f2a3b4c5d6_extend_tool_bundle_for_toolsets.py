"""extend tool_bundle for toolsets

Revision ID: e1f2a3b4c5d6
Revises: d7e8f9a0b1c2
Create Date: 2026-03-11 11:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # Make user_id nullable (inner bundles have no owner)
    conn.execute(sa.text("ALTER TABLE tool_bundle ALTER COLUMN user_id DROP NOT NULL"))

    # Add bundle_type and source columns
    conn.execute(sa.text(
        "ALTER TABLE tool_bundle ADD COLUMN IF NOT EXISTS bundle_type VARCHAR(20) NOT NULL DEFAULT 'user'"
    ))
    conn.execute(sa.text(
        "ALTER TABLE tool_bundle ADD COLUMN IF NOT EXISTS source VARCHAR(255)"
    ))

    # Drop old unique constraint (user_id, name) — no longer globally unique
    conn.execute(sa.text("DROP INDEX IF EXISTS ux_tool_bundle_user_name"))

    # Partial unique indexes per bundle_type
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_tool_bundle_user_name "
        "ON tool_bundle (user_id, name) WHERE bundle_type = 'user'"
    ))
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_tool_bundle_inner_source "
        "ON tool_bundle (source) WHERE bundle_type = 'inner'"
    ))
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_tool_bundle_mcp_user_source "
        "ON tool_bundle (user_id, source) WHERE bundle_type = 'mcp'"
    ))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("DROP INDEX IF EXISTS ux_tool_bundle_mcp_user_source"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ux_tool_bundle_inner_source"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ux_tool_bundle_user_name"))

    conn.execute(sa.text("ALTER TABLE tool_bundle DROP COLUMN IF EXISTS source"))
    conn.execute(sa.text("ALTER TABLE tool_bundle DROP COLUMN IF EXISTS bundle_type"))

    # Restore NOT NULL on user_id
    conn.execute(sa.text(
        "UPDATE tool_bundle SET user_id = '00000000-0000-0000-0000-000000000000' WHERE user_id IS NULL"
    ))
    conn.execute(sa.text("ALTER TABLE tool_bundle ALTER COLUMN user_id SET NOT NULL"))

    # Restore original unique index
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_tool_bundle_user_name ON tool_bundle (user_id, name)"
    ))
