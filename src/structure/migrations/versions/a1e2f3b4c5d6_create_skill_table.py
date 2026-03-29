"""create skill table

Revision ID: a1e2f3b4c5d6
Revises: c9e09d25a17f
Create Date: 2026-02-18 16:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a1e2f3b4c5d6'
down_revision: str | Sequence[str] | None = 'c9e09d25a17f'
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
            embedding_384 vector(384),
            embedding_768 vector(768),
            embedding_1024 vector(1024),
            embedding_1536 vector(1536),
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
