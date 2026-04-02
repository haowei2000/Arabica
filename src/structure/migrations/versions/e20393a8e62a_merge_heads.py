"""merge_heads

Revision ID: e20393a8e62a
Revises: f2e3d4c5b6a7
Create Date: 2026-03-14 09:33:16.651427

"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e20393a8e62a'
down_revision: str | Sequence[str] | None = 'f2e3d4c5b6a7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
