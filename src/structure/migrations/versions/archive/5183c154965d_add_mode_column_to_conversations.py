"""add_mode_column_to_conversations

Revision ID: 5183c154965d
Revises: 4f1d2a3b5c6d
Create Date: 2026-01-09 00:26:07.316491

"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "5183c154965d"
down_revision: str | Sequence[str] | None = "4f1d2a3b5c6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add mode column with default value 'chat'
    op.add_column(
        "conversations",
        sa.Column("mode", sa.String(length=50), nullable=False, server_default="chat"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Remove mode column
    op.drop_column("conversations", "mode")
