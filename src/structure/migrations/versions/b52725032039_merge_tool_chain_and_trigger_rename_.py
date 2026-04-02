"""merge tool chain and trigger rename branches

Revision ID: b52725032039
Revises: 0c8410a84138, 405c96961f3d
Create Date: 2026-02-26 13:59:32.168663

"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b52725032039'
down_revision: str | Sequence[str] | None = '0c8410a84138'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
