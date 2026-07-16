"""remove per-model LLM API configuration columns

Revision ID: u6p7q8r9s0t1
Revises: t5o6p7q8r9s0
Create Date: 2026-05-26 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "u6p7q8r9s0t1"
down_revision: str | Sequence[str] | None = "t5o6p7q8r9s0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(
        column["name"] == column_name for column in inspector.get_columns(table_name)
    )


def upgrade() -> None:
    """Keep runtime LLM API configuration exclusively in OPENAI__ env vars."""
    for table_name in ("chat_model", "embedding_model"):
        if _has_column(table_name, "base_url"):
            op.drop_column(table_name, "base_url")
        if _has_column(table_name, "api_key_ref"):
            op.drop_column(table_name, "api_key_ref")


def downgrade() -> None:
    for table_name in ("chat_model", "embedding_model"):
        if not _has_column(table_name, "base_url"):
            op.add_column(
                table_name, sa.Column("base_url", sa.String(500), nullable=True)
            )
        if not _has_column(table_name, "api_key_ref"):
            op.add_column(
                table_name,
                sa.Column("api_key_ref", sa.String(255), nullable=True),
            )
