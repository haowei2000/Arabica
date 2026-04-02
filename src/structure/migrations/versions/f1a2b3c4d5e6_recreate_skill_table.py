"""recreate skill table (was dropped by eb254ab4cc1b by mistake)

Revision ID: f1a2b3c4d5e6
Revises: a60e2257547b
Create Date: 2026-02-19 12:40:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'f1a2b3c4d5e6'
down_revision: str | Sequence[str] | None = 'a60e2257547b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS skill (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            content TEXT NOT NULL,
            glance TEXT,
            summary TEXT,
            path VARCHAR(500),
            source_id UUID,
            tags JSONB,
            meta JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_skill_user_id ON skill (user_id)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_skill_user_id_name ON skill (user_id, name)"))


def downgrade() -> None:
    op.drop_index('ix_skill_user_id_name', table_name='skill')
    op.drop_index('ix_skill_user_id', table_name='skill')
    op.drop_table('skill')
