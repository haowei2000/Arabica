"""create chat_model and embedding_model tables

Revision ID: m8h9i0j1k2l3
Revises: l7g8h9i0j1k2
Create Date: 2026-03-29 00:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "m8h9i0j1k2l3"
down_revision: str | None = "l7g8h9i0j1k2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "chat_model" in existing_tables and "embedding_model" in existing_tables:
        return

    # Create chat_model table
    op.create_table(
        "chat_model",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("api_key_ref", sa.String(255), nullable=True),
        sa.Column("max_tokens", sa.Integer, nullable=True),
        sa.Column("context_window", sa.Integer, nullable=True),
        sa.Column(
            "supports_vision", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column(
            "supports_function_call", sa.Boolean, nullable=False, server_default="true"
        ),
        sa.Column(
            "supports_streaming", sa.Boolean, nullable=False, server_default="true"
        ),
        sa.Column("default_temperature", sa.Float, nullable=True),
        sa.Column("default_top_p", sa.Float, nullable=True),
        sa.Column("default_max_tokens", sa.Integer, nullable=True),
        sa.Column("input_price", sa.Float, nullable=True),
        sa.Column("output_price", sa.Float, nullable=True),
        sa.Column("currency", sa.String(10), nullable=False, server_default="USD"),
        sa.Column("config", JSONB, nullable=True),
        sa.Column("meta", JSONB, nullable=True),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_chat_model_user_id", "chat_model", ["user_id"])
    op.create_index("ix_chat_model_provider", "chat_model", ["provider"])

    # Create embedding_model table
    op.create_table(
        "embedding_model",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("api_key_ref", sa.String(255), nullable=True),
        sa.Column("dimension", sa.Integer, nullable=False),
        sa.Column("max_tokens", sa.Integer, nullable=True),
        sa.Column("supports_batch", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("batch_size", sa.Integer, nullable=False, server_default="32"),
        sa.Column("normalize", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "distance_metric", sa.String(20), nullable=False, server_default="cosine"
        ),
        sa.Column("price", sa.Float, nullable=True),
        sa.Column("currency", sa.String(10), nullable=False, server_default="USD"),
        sa.Column("config", JSONB, nullable=True),
        sa.Column("meta", JSONB, nullable=True),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_embedding_model_user_id", "embedding_model", ["user_id"])
    op.create_index("ix_embedding_model_provider", "embedding_model", ["provider"])


def downgrade() -> None:
    op.drop_table("embedding_model")
    op.drop_table("chat_model")
