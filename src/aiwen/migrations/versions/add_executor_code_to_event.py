"""add executor_code to event

Revision ID: add_executor_code_001
Revises:
Create Date: 2025-02-08 12:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "add_executor_code_001"
down_revision: str | None = "f08172d65fba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add executor_code column to event table."""
    # Add executor_code column
    op.add_column(
        "event",
        sa.Column(
            "executor_code",
            sa.String(length=100),
            nullable=True,
            comment="执行器模板代码（处理此事件的 Agent 模板）",
        ),
    )

    # Create index for executor_code
    op.create_index("ix_event_executor_code", "event", ["executor_code"], unique=False)


def downgrade() -> None:
    """Remove executor_code column from event table."""
    # Drop index first
    op.drop_index("ix_event_executor_code", table_name="event")

    # Remove column
    op.drop_column("event", "executor_code")
