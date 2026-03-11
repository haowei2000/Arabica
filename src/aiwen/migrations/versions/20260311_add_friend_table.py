"""Add auth_friend table for friend feature.

Revision ID: 20260311_add_friend_table
Revises: 20260301_baseline
Create Date: 2026-03-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260311_add_friend_table"
down_revision = "20260301_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_friend",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            comment="Friend record unique ID",
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="User who initiated the friend request",
        ),
        sa.Column(
            "friend_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="User who received the friend request",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
            comment="Friendship status: pending/accepted/declined/blocked",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Creation time",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Update time",
        ),
        sa.UniqueConstraint("user_id", "friend_id", name="uq_auth_friend_pair"),
    )

    op.create_index("ix_auth_friend_user", "auth_friend", ["user_id"])
    op.create_index("ix_auth_friend_friend", "auth_friend", ["friend_id"])
    op.create_index("ix_auth_friend_status", "auth_friend", ["status"])


def downgrade() -> None:
    op.drop_index("ix_auth_friend_status", table_name="auth_friend")
    op.drop_index("ix_auth_friend_friend", table_name="auth_friend")
    op.drop_index("ix_auth_friend_user", table_name="auth_friend")
    op.drop_table("auth_friend")
