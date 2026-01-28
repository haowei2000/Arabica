"""add_from_end_user_id_column_to_conversations_table

Revision ID: 593829819d41
Revises: 13a3d8021490
Create Date: 2025-12-23 17:48:03.283778

"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "593829819d41"
down_revision: str | Sequence[str] | None = "13a3d8021490"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add the from_end_user_id column to conversations table
    op.add_column(
        "conversations", sa.Column("from_end_user_id", sa.UUID(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop the from_end_user_id column from conversations table
    op.drop_column("conversations", "from_end_user_id")
