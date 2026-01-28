"""make app_id column nullable in messages table

Revision ID: 4f1d2a3b5c6d
Revises: 0430e7390877
Create Date: 2025-12-31 10:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4f1d2a3b5c6d"
down_revision = "0430e7390877"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Make app_id column nullable in messages table"""
    op.alter_column("messages", "app_id", nullable=True)


def downgrade() -> None:
    """Revert app_id column to not nullable in messages table"""
    op.alter_column("messages", "app_id", nullable=False)
