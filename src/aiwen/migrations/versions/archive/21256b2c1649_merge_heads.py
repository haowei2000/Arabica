"""merge heads

Revision ID: 21256b2c1649
Revises: 3a54b027c7d6, e5f6a7b8c9d0
Create Date: 2026-01-27 21:43:13.345325

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '21256b2c1649'
down_revision: Union[str, Sequence[str], None] = ('3a54b027c7d6', 'e5f6a7b8c9d0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
