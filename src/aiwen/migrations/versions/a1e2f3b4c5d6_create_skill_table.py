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
    op.create_table(
        'skill',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('glance', sa.Text, nullable=True),
        sa.Column('summary', sa.Text, nullable=True),
        sa.Column('path', sa.String(500), nullable=True),
        sa.Column('source_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('tags', postgresql.JSONB, nullable=True),
        sa.Column('meta', postgresql.JSONB, nullable=True),
        sa.Column('embedding_384', Vector(384), nullable=True),
        sa.Column('embedding_768', Vector(768), nullable=True),
        sa.Column('embedding_1024', Vector(1024), nullable=True),
        sa.Column('embedding_1536', Vector(1536), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_skill_user_id', 'skill', ['user_id'])
    op.create_index('ix_skill_user_id_name', 'skill', ['user_id', 'name'])


def downgrade() -> None:
    op.drop_index('ix_skill_user_id_name', table_name='skill')
    op.drop_index('ix_skill_user_id', table_name='skill')
    op.drop_table('skill')
