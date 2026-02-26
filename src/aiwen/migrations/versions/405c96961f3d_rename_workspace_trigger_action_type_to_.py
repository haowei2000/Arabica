"""rename workspace_trigger action_type to tool_name

Revision ID: 405c96961f3d
Revises: h3c4d5e6f7g8
Create Date: 2026-02-22 18:04:53.117038

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '405c96961f3d'
down_revision: Union[str, Sequence[str], None] = 'h3c4d5e6f7g8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename action_type -> tool_name on workspace_trigger."""
    op.alter_column(
        'workspace_trigger',
        'action_type',
        new_column_name='tool_name',
        existing_type=sa.String(length=100),
        existing_nullable=False,
        comment='要执行的工具名称（任意已注册工具，如 glance_context / http_request 等）',
    )


def downgrade() -> None:
    """Rename tool_name -> action_type on workspace_trigger."""
    op.alter_column(
        'workspace_trigger',
        'tool_name',
        new_column_name='action_type',
        existing_type=sa.String(length=100),
        existing_nullable=False,
        comment='动作类型: read_context | list_context | glance_context | glob_context | search_context',
    )
