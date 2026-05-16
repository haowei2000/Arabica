"""add workspace context version

Revision ID: s4n5o6p7q8r9
Revises: r3m4n5o6p7q8
Create Date: 2026-05-16 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "s4n5o6p7q8r9"
down_revision: str | Sequence[str] | None = "r3m4n5o6p7q8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace",
        sa.Column(
            "context_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
            comment="工作空间上下文缓存版本",
        ),
    )


def downgrade() -> None:
    op.drop_column("workspace", "context_version")

