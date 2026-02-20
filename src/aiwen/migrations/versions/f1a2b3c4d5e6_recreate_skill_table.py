"""recreate skill table (was dropped by eb254ab4cc1b by mistake)

Revision ID: f1a2b3c4d5e6
Revises: a60e2257547b
Create Date: 2026-02-19 12:40:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f1a2b3c4d5e6'
down_revision: str | Sequence[str] | None = 'a60e2257547b'
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
