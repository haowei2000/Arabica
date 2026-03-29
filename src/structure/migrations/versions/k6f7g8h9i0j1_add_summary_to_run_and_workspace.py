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
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE run ADD COLUMN IF NOT EXISTS title VARCHAR(255)"))
    conn.execute(sa.text("COMMENT ON COLUMN run.title IS 'LLM生成的标题'"))
    conn.execute(sa.text("ALTER TABLE run ADD COLUMN IF NOT EXISTS summary TEXT"))
    conn.execute(sa.text("COMMENT ON COLUMN run.summary IS 'LLM生成的摘要'"))
    conn.execute(sa.text("ALTER TABLE workspace ADD COLUMN IF NOT EXISTS summary TEXT"))
    conn.execute(sa.text("COMMENT ON COLUMN workspace.summary IS 'LLM生成的工作空间摘要'"))


def downgrade() -> None:
    op.drop_column("workspace", "summary")
    op.drop_column("run", "summary")
    op.drop_column("run", "title")
