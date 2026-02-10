"""unify tool and user_tools tables

Merge the user_tools table into the tool table. Add all user_tools columns
to the tool table, migrate existing rows, then drop user_tools.

Revision ID: a1b2c3d4e5f6
Revises: fc7024d34fc3
Create Date: 2026-02-09 18:00:00.000000
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "fc7024d34fc3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add new columns to tool, migrate user_tools data, drop user_tools."""

    # ── Step 1: Add columns from user_tools to tool ──────────────────────
    op.add_column(
        "tool",
        sa.Column(
            "display_name", sa.String(200), nullable=True, comment="Display name"
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "execution_mode",
            sa.String(50),
            nullable=True,
            comment="Execution mode: server_run, http, client_run, container_run, celery_run",
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "inner_tool_name",
            sa.String(100),
            nullable=True,
            comment="Name of the InnerTool to delegate to",
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "parameter_mapping",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Maps external param names to InnerTool param names",
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "output_schema",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Output schema (optional)",
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "code", sa.Text(), nullable=True, comment="Python code for server_run mode"
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "http_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="HTTP configuration for http mode",
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "container_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Container configuration",
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "client_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Client configuration",
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "celery_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Celery configuration",
        ),
    )
    op.add_column(
        "tool",
        sa.Column("category", sa.String(50), nullable=True, comment="Tool category"),
    )
    op.add_column(
        "tool",
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Tool tags",
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "timeout",
            sa.Integer(),
            nullable=True,
            server_default="30",
            comment="Execution timeout in seconds",
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "verified",
            sa.Boolean(),
            nullable=True,
            server_default="false",
            comment="Whether tool is verified by admin",
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "usage_count",
            sa.Integer(),
            nullable=True,
            server_default="0",
            comment="Number of times tool has been used",
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Last used timestamp",
        ),
    )
    op.add_column(
        "tool",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Workspace ID (optional)",
        ),
    )

    # ── Step 2: Normalize existing tool_type values → 'external' ─────────
    op.execute(
        "UPDATE tool SET tool_type = 'external' "
        "WHERE tool_type NOT IN ('inner', 'external')"
    )

    # ── Step 3: Migrate rows from user_tools → tool ──────────────────────
    # Use ut.id hex suffix to avoid tool_code collisions with existing tool rows
    op.execute("""
        INSERT INTO tool (
            id, name, tool_code, description, tool_type,
            input_schema, config, user_id, enabled, is_public, version,
            created_at, updated_at,
            display_name, execution_mode, inner_tool_name, parameter_mapping,
            output_schema, code, http_config, container_config, client_config,
            celery_config, category, tags, timeout, verified,
            usage_count, last_used_at, workspace_id
        )
        SELECT
            ut.id,
            ut.name,
            'ext_' || ut.name || '_' || left(ut.id::text, 8) AS tool_code,
            ut.description,
            'external' AS tool_type,
            ut.input_schema,
            NULL AS config,
            ut.user_id,
            ut.enabled,
            ut.is_public,
            1 AS version,
            ut.created_at,
            ut.updated_at,
            ut.display_name,
            ut.execution_mode,
            ut.inner_tool_name,
            ut.parameter_mapping,
            ut.output_schema,
            ut.code,
            ut.http_config,
            ut.container_config,
            ut.client_config,
            ut.celery_config,
            ut.category,
            ut.tags,
            ut.timeout,
            ut.verified,
            ut.usage_count,
            ut.last_used_at,
            ut.workspace_id
        FROM user_tools ut
        WHERE ut.id NOT IN (SELECT id FROM tool)
    """)

    # ── Step 4: Create indexes ────────────────────────────────────────────
    op.create_index(
        "ix_tool_name_inner",
        "tool",
        ["name"],
        unique=True,
        postgresql_where=sa.text("tool_type = 'inner'"),
    )
    op.create_index(
        "ix_tool_user_name_external",
        "tool",
        ["user_id", "name"],
        unique=True,
        postgresql_where=sa.text("tool_type = 'external'"),
    )
    op.create_index("ix_tool_tool_type", "tool", ["tool_type"])
    op.create_index("ix_tool_workspace_id", "tool", ["workspace_id"])

    # ── Step 5: Drop user_tools table ─────────────────────────────────────
    op.drop_table("user_tools")


def downgrade() -> None:
    """Recreate user_tools and move external rows back."""

    # Recreate user_tools table
    op.create_table(
        "user_tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "execution_mode", sa.String(50), nullable=False, server_default="server_run"
        ),
        sa.Column("inner_tool_name", sa.String(100), nullable=True),
        sa.Column(
            "parameter_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "input_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "output_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column(
            "http_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "container_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "client_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "celery_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("category", sa.String(50), server_default="custom"),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("version", sa.String(20), server_default="1.0.0"),
        sa.Column("timeout", sa.Integer(), server_default="30"),
        sa.Column("enabled", sa.Boolean(), server_default="true"),
        sa.Column("is_public", sa.Boolean(), server_default="false"),
        sa.Column("verified", sa.Boolean(), server_default="false"),
        sa.Column("usage_count", sa.Integer(), server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Move external rows back
    op.execute("""
        INSERT INTO user_tools (
            id, user_id, workspace_id, name, display_name, description,
            execution_mode, inner_tool_name, parameter_mapping,
            input_schema, output_schema, code, http_config,
            container_config, client_config, celery_config,
            category, tags, timeout, enabled, is_public, verified,
            usage_count, last_used_at, created_at, updated_at
        )
        SELECT
            id, user_id, workspace_id, name, display_name, description,
            execution_mode, inner_tool_name, parameter_mapping,
            input_schema, output_schema, code, http_config,
            container_config, client_config, celery_config,
            category, tags, timeout, enabled, is_public, verified,
            usage_count, last_used_at, created_at, updated_at
        FROM tool
        WHERE tool_type = 'external'
          AND display_name IS NOT NULL
    """)

    # Delete migrated rows from tool
    op.execute(
        "DELETE FROM tool WHERE tool_type = 'external' AND display_name IS NOT NULL"
    )

    # Recreate user_tools indexes
    op.create_index("ix_user_tools_user_id", "user_tools", ["user_id"])
    op.create_index("ix_user_tools_workspace_id", "user_tools", ["workspace_id"])
    op.create_index("ix_user_tools_name", "user_tools", ["name"])
    op.create_index("ix_user_tools_enabled", "user_tools", ["enabled"])
    op.create_index("ix_user_tools_is_public", "user_tools", ["is_public"])
    op.create_index(
        "ix_user_tools_user_name", "user_tools", ["user_id", "name"], unique=True
    )

    # Drop new indexes from tool
    op.drop_index("ix_tool_workspace_id", table_name="tool")
    op.drop_index("ix_tool_tool_type", table_name="tool")
    op.drop_index("ix_tool_user_name_external", table_name="tool")
    op.drop_index("ix_tool_name_inner", table_name="tool")

    # Drop new columns from tool
    for col in [
        "display_name",
        "execution_mode",
        "inner_tool_name",
        "parameter_mapping",
        "output_schema",
        "code",
        "http_config",
        "container_config",
        "client_config",
        "celery_config",
        "category",
        "tags",
        "timeout",
        "verified",
        "usage_count",
        "last_used_at",
        "workspace_id",
    ]:
        op.drop_column("tool", col)
