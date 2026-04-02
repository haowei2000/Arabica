"""add token columns to run

Revision ID: g3f4e5d6c7b8
Revises: f2e3d4c5b6a7
Create Date: 2026-03-11 13:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "g3f4e5d6c7b8"
down_revision: str | tuple[str, ...] | None = "f2e3d4c5b6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("run")}
    if "input_tokens" not in existing_columns:
        op.add_column(
            "run",
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        )
    if "output_tokens" not in existing_columns:
        op.add_column(
            "run",
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.drop_column("run", "output_tokens")
    op.drop_column("run", "input_tokens")
