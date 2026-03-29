"""merge_token_columns_and_heads

Revision ID: e57d623197a6
Revises: e20393a8e62a, h4g5f6e7d8c9
Create Date: 2026-03-15 17:13:49.088100

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e57d623197a6'
down_revision: Union[str, Sequence[str], None] = ('e20393a8e62a', 'h4g5f6e7d8c9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
