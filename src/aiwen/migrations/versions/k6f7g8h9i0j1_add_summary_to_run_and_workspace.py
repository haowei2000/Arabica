"""add title/summary to run and summary to workspace

Revision ID: k6f7g8h9i0j1
Revises: j5e6f7g8h9i0
Create Date: 2026-03-22 00:01:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "k6f7g8h9i0j1"
down_revision: Union[str, None] = "j5e6f7g8h9i0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "run",
        sa.Column("title", sa.String(255), nullable=True, comment="LLM生成的标题"),
    )
    op.add_column(
        "run",
        sa.Column("summary", sa.Text(), nullable=True, comment="LLM生成的摘要"),
    )
    op.add_column(
        "workspace",
        sa.Column("summary", sa.Text(), nullable=True, comment="LLM生成的工作空间摘要"),
    )


def downgrade() -> None:
    op.drop_column("workspace", "summary")
    op.drop_column("run", "summary")
    op.drop_column("run", "title")
