"""drop skill embedding columns

Embeddings for skills are now stored in the Context table (via sync_skill_to_contexts).
The embedding_* columns on the skill table were never populated and are no longer needed.

Revision ID: b2f3e4c5d6e7
Revises: a1e2f3b4c5d6
Create Date: 2026-02-18 18:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "b2f3e4c5d6e7"
down_revision: str | Sequence[str] | None = "a1e2f3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE skill DROP COLUMN IF EXISTS embedding_384"))
    conn.execute(sa.text("ALTER TABLE skill DROP COLUMN IF EXISTS embedding_768"))
    conn.execute(sa.text("ALTER TABLE skill DROP COLUMN IF EXISTS embedding_1024"))
    conn.execute(sa.text("ALTER TABLE skill DROP COLUMN IF EXISTS embedding_1536"))


def downgrade() -> None:
    from pgvector.sqlalchemy import Vector

    op.add_column("skill", sa.Column("embedding_384", Vector(384), nullable=True))
    op.add_column("skill", sa.Column("embedding_768", Vector(768), nullable=True))
    op.add_column("skill", sa.Column("embedding_1024", Vector(1024), nullable=True))
    op.add_column("skill", sa.Column("embedding_1536", Vector(1536), nullable=True))
