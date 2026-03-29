"""add user_id column to agent_task table

Revision ID: a1b2c3d4e5f6
Revises: 5183c154965d
Create Date: 2026-01-11 00:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "5183c154965d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add user_id column to agent_task table
    # Note: Adding as nullable first to handle existing data
    # You may need to update existing rows before making it NOT NULL
    op.add_column(
        "agent_task",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="关联的用户标识",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Remove user_id column from agent_task table
    op.drop_column("agent_task", "user_id")
