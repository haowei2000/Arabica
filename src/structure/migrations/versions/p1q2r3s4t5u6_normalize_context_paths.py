"""normalize context paths to leading-slash form

Revision ID: p1q2r3s4t5u6
Revises: o0j1k2l3m4n5
Create Date: 2026-05-06 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "p1q2r3s4t5u6"
down_revision: str | Sequence[str] | None = "o0j1k2l3m4n5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_check_constraint(inspector: sa.Inspector, table: str, name: str) -> bool:
    return any(
        constraint["name"] == name
        for constraint in inspector.get_check_constraints(table)
    )


def _normalize_semantic_roots(table: str) -> None:
    op.execute(
        f"""
        UPDATE {table}
           SET path = '/tools' || substring(path from 6)
         WHERE path = '/tool' OR path LIKE '/tool/%'
        """
    )
    op.execute(
        f"""
        UPDATE {table}
           SET path = '/skills' || substring(path from 7)
         WHERE path = '/skill' OR path LIKE '/skill/%'
        """
    )
    op.execute(
        f"""
        UPDATE {table}
           SET path = '/memory' || substring(path from 10)
         WHERE path = '/memories' OR path LIKE '/memories/%'
        """
    )


def _normalize_semantic_segments(table: str) -> None:
    op.execute(
        f"""
        UPDATE {table}
           SET path = regexp_replace(replace(path, '_', '-'), '-+', '-', 'g')
         WHERE path ~ '^/(tools|skills|knowledge|memory|workspaces|runs|triggers)(/|$)'
        """
    )


def _normalize_tool_schema_paths(table: str, discriminator: str) -> None:
    op.execute(
        f"""
        UPDATE {table}
           SET path = path || '/schema.md'
         WHERE {discriminator}
           AND path ~ '^/tools/[^/]+$'
        """
    )


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if "context" in tables:
        op.execute("UPDATE context SET path = btrim(path) WHERE path IS NOT NULL")
        op.execute("UPDATE context SET path = '/' WHERE path = ''")
        op.execute(
            """
            UPDATE context
               SET path = '/' || ltrim(path, '/')
             WHERE path IS NOT NULL
               AND path != ''
               AND path NOT LIKE '/%'
            """
        )
        _normalize_semantic_roots("context")
        _normalize_semantic_segments("context")
        _normalize_tool_schema_paths("context", "context_type = 'tool'")
        if not _has_check_constraint(
            inspector, "context", "ck_context_path_leading_slash"
        ):
            op.create_check_constraint(
                "ck_context_path_leading_slash",
                "context",
                "path IS NULL OR path = '/' OR path LIKE '/%'",
            )

    if "workspace_context" in tables:
        op.execute(
            "UPDATE workspace_context SET path = btrim(path) WHERE path IS NOT NULL"
        )
        op.execute("UPDATE workspace_context SET path = '/' WHERE path = ''")
        op.execute(
            """
            UPDATE workspace_context
               SET path = '/' || ltrim(path, '/')
             WHERE path IS NOT NULL
               AND path != ''
               AND path NOT LIKE '/%'
            """
        )
        _normalize_semantic_roots("workspace_context")
        _normalize_semantic_segments("workspace_context")
        _normalize_tool_schema_paths("workspace_context", "meta ? 'tool_id'")
        if not _has_check_constraint(
            inspector, "workspace_context", "ck_workspace_context_path_leading_slash"
        ):
            op.create_check_constraint(
                "ck_workspace_context_path_leading_slash",
                "workspace_context",
                "path IS NULL OR path = '/' OR path LIKE '/%'",
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if (
        "workspace_context" in tables
        and _has_check_constraint(
            inspector, "workspace_context", "ck_workspace_context_path_leading_slash"
        )
    ):
        op.drop_constraint(
            "ck_workspace_context_path_leading_slash",
            "workspace_context",
            type_="check",
        )

    if "context" in tables and _has_check_constraint(
        inspector, "context", "ck_context_path_leading_slash"
    ):
        op.drop_constraint(
            "ck_context_path_leading_slash",
            "context",
            type_="check",
        )
