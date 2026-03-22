"""add scope column to context table

Revision ID: j5e6f7g8h9i0
Revises: e57d623197a6
Create Date: 2026-03-22 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j5e6f7g8h9i0"
down_revision: Union[str, None] = "e57d623197a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "context",
        sa.Column(
            "scope",
            sa.String(length=20),
            nullable=False,
            server_default="user",
            comment="可见范围: user（仅自己）/ workspace（工作空间）/ global（全局）",
        ),
    )
    op.create_index("ix_context_scope", "context", ["scope"])
    op.create_index("ix_context_user_scope", "context", ["user_id", "scope"])


def downgrade() -> None:
    op.drop_index("ix_context_user_scope", table_name="context")
    op.drop_index("ix_context_scope", table_name="context")
    op.drop_column("context", "scope")
