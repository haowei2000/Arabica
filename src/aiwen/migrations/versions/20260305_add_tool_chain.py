"""Add chain column to tool table.

Supports pipeline execution mode where an ExternalTool defines
an ordered list of ChainSteps that are executed sequentially,
piping output data forward.

Revision ID: 20260305_add_chain
Revises: 20260305_drop_exec_mode
Create Date: 2026-03-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "20260305_add_chain"
down_revision = "20260305_drop_exec_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tool",
        sa.Column(
            "chain",
            JSONB,
            nullable=True,
            comment="Ordered list of chain steps: [{tool_name, parameter_mapping, extra_params}]",
        ),
    )


def downgrade() -> None:
    op.drop_column("tool", "chain")
