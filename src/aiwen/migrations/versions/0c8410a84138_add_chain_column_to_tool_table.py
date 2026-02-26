"""add_chain_column_to_tool_table

Revision ID: 0c8410a84138
Revises: h3c4d5e6f7g8
Create Date: 2026-02-26 09:05:04.880621

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0c8410a84138'
down_revision: Union[str, Sequence[str], None] = 'h3c4d5e6f7g8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'tool',
        sa.Column(
            'chain',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='Ordered list of chain steps: [{tool_name, parameter_mapping, extra_params}]',
        ),
    )
    op.execute("ALTER TABLE tool DROP COLUMN IF EXISTS execution_mode")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tool', 'chain')
    op.add_column(
        'tool',
        sa.Column(
            'execution_mode',
            sa.VARCHAR(length=50),
            nullable=True,
            comment='Execution mode: server_run, http, client_run, container_run, celery_run',
        ),
    )
