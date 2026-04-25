"""add rating columns to context

Adds ``rating_sum`` and ``rating_count`` to ``context`` so the denormalised
aggregate of CONTEXT_RATED events can be read in a single row fetch.  The
event log is still the source of truth; these columns exist only as a fast
lookup for retrieval-time sorting and thresholding.

Revision ID: o0j1k2l3m4n5
Revises: n9i0j1k2l3m4
Create Date: 2026-04-23 23:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "o0j1k2l3m4n5"
down_revision: str | Sequence[str] | None = "n9i0j1k2l3m4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("context")}
    if "rating_sum" not in existing_columns:
        op.add_column(
            "context",
            sa.Column(
                "rating_sum",
                sa.Float(),
                nullable=False,
                server_default="0",
            ),
        )
    if "rating_count" not in existing_columns:
        op.add_column(
            "context",
            sa.Column(
                "rating_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    existing_indexes = {ix["name"] for ix in inspector.get_indexes("context")}
    if "ix_context_rating" not in existing_indexes:
        op.create_index(
            "ix_context_rating",
            "context",
            ["rating_count", "rating_sum"],
        )


def downgrade() -> None:
    op.drop_index("ix_context_rating", table_name="context")
    op.drop_column("context", "rating_count")
    op.drop_column("context", "rating_sum")
