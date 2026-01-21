"""add agent_template_id column and remove app_type from app table

Revision ID: da790b82e86b
Revises: 0001
Create Date: 2025-12-23 14:45:19.297075

"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'da790b82e86b'
down_revision: str | Sequence[str] | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add the new agent_template_id column to the app table
    op.add_column('app', sa.Column('agent_template_id', sa.UUID(), nullable=True, comment='构建该app使用的模板ID'))

    # Drop the app_type column from the app table
    op.drop_column('app', 'app_type')


def downgrade() -> None:
    """Downgrade schema."""
    # Add back the app_type column
    op.add_column('app', sa.Column('app_type', sa.VARCHAR(), autoincrement=False, nullable=False))

    # Drop the agent_template_id column
    op.drop_column('app', 'agent_template_id')
