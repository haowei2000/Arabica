"""create artifact and task tables

Revision ID: d1e2f3a4b5c6
Revises: b52725032039
Create Date: 2026-02-28 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "b52725032039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create artifact and task tables."""
    conn = op.get_bind()
    # --- artifact table ---
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS artifact (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            run_id UUID REFERENCES run(id) ON DELETE SET NULL,
            name VARCHAR(512) NOT NULL,
            artifact_type VARCHAR(50) NOT NULL DEFAULT 'text',
            content_type VARCHAR(255),
            content TEXT,
            s3_key VARCHAR(1024),
            s3_url VARCHAR(2048),
            version INTEGER NOT NULL DEFAULT 1,
            meta JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_artifact_workspace ON artifact (workspace_id, created_at)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_artifact_run ON artifact (run_id, created_at)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_artifact_type ON artifact (artifact_type)"))

    # --- task table ---
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS task (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspace(id) ON DELETE CASCADE,
            run_id UUID REFERENCES run(id) ON DELETE SET NULL,
            parent_task_id UUID REFERENCES task(id) ON DELETE SET NULL,
            title VARCHAR(512) NOT NULL,
            description TEXT,
            result TEXT,
            status VARCHAR(50) NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 2,
            assignee VARCHAR(255),
            meta JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_task_workspace ON task (workspace_id, created_at)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_task_run ON task (run_id, status)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_task_status ON task (status, priority)"))
    conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_task_parent ON task (parent_task_id)"))


def downgrade() -> None:
    """Drop artifact and task tables."""
    conn = op.get_bind()
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_task_parent"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_task_status"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_task_run"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_task_workspace"))
    conn.execute(sa.text("DROP TABLE IF EXISTS task"))

    conn.execute(sa.text("DROP INDEX IF EXISTS ix_artifact_type"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_artifact_run"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_artifact_workspace"))
    conn.execute(sa.text("DROP TABLE IF EXISTS artifact"))
