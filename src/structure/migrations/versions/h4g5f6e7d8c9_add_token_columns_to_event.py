"""add token columns to event

Revision ID: h4g5f6e7d8c9
Revises: g3f4e5d6c7b8
Create Date: 2026-03-11 14:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "h4g5f6e7d8c9"
down_revision: str | tuple[str, ...] | None = "g3f4e5d6c7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("event")}
    if "input_tokens" not in existing_columns:
        op.add_column(
            "event",
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        )
    if "output_tokens" not in existing_columns:
        op.add_column(
            "event",
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.drop_column("event", "output_tokens")
    op.drop_column("event", "input_tokens")
