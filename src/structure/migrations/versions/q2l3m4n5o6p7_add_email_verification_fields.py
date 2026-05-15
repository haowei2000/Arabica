"""add email verification fields

Revision ID: q2l3m4n5o6p7
Revises: p1k2l3m4n5o6
Create Date: 2026-05-16 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "q2l3m4n5o6p7"
down_revision: str | Sequence[str] | None = "p1k2l3m4n5o6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("auth_user")}

    if "email_verified" not in existing_columns:
        op.add_column(
            "auth_user",
            sa.Column(
                "email_verified",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
        op.execute(
            sa.text(
                "UPDATE auth_user SET email_verified = true "
                "WHERE email IS NOT NULL AND email <> ''"
            )
        )

    if "email_verified_at" not in existing_columns:
        op.add_column(
            "auth_user",
            sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(
            sa.text(
                "UPDATE auth_user SET email_verified_at = updated_at "
                "WHERE email_verified = true AND email_verified_at IS NULL"
            )
        )


def downgrade() -> None:
    op.drop_column("auth_user", "email_verified_at")
    op.drop_column("auth_user", "email_verified")
