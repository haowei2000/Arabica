"""add user_id column to app table

Revision ID: 13a3d8021490
Revises: dd983e0a2018
Create Date: 2025-12-23 16:51:54.152154

"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '13a3d8021490'
down_revision: str | Sequence[str] | None = 'dd983e0a2018'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add user_id column to app table only
    op.add_column('app', sa.Column('user_id', sa.UUID(), nullable=True, comment='创建该app的用户ID'))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove user_id column from app table
    op.drop_column('app', 'user_id')
