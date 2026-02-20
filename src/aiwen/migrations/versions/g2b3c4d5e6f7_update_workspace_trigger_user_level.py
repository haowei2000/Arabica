"""update workspace_trigger for user-level trigger templates

Revision ID: g2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-02-19 14:00:00.000000

Changes:
- Make workspace_id nullable (user-level templates have workspace_id=null)
- Add user_id column for template ownership
- Add index on user_id
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'g2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Make workspace_id nullable and add user_id column (idempotent)."""
    conn = op.get_bind()

    # Make workspace_id nullable if it isn't already
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'workspace_trigger'
                AND column_name = 'workspace_id'
                AND is_nullable = 'NO'
            ) THEN
                ALTER TABLE workspace_trigger ALTER COLUMN workspace_id DROP NOT NULL;
            END IF;
        END
        $$;
    """))

    # Add user_id column if it doesn't exist
    conn.execute(sa.text("""
        ALTER TABLE workspace_trigger
        ADD COLUMN IF NOT EXISTS user_id UUID;
    """))

    # Add comment on user_id
    conn.execute(sa.text("""
        COMMENT ON COLUMN workspace_trigger.user_id
        IS '创建者用户ID（用于用户级模板）';
    """))

    # Add index on user_id if it doesn't exist
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_workspace_trigger_user_id
        ON workspace_trigger (user_id);
    """))


def downgrade() -> None:
    """Reverse user-level trigger changes."""
    op.drop_index('ix_workspace_trigger_user_id', table_name='workspace_trigger')

    op.drop_column('workspace_trigger', 'user_id')

    # Restore workspace_id as NOT NULL
    op.alter_column(
        'workspace_trigger',
        'workspace_id',
        existing_type=sa.UUID(),
        nullable=False,
        comment='所属工作区ID',
    )
