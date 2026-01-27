"""change_message_column_to_list_of_dicts

Revision ID: 0236573bfc43
Revises: b2c3d4e5f6a7
Create Date: 2026-01-22 11:24:27.215195

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0236573bfc43"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Convert message column from dict to list[dict] format."""
    # Convert existing dict data to list[dict] by wrapping in array
    # Only convert if the data is not already an array
    op.execute("""
        UPDATE messages
        SET message = jsonb_build_array(message::jsonb)::json
        WHERE message IS NOT NULL
        AND jsonb_typeof(message::jsonb) = 'object'
    """)


def downgrade() -> None:
    """Convert message column from list[dict] back to dict format."""
    # Extract first element from array
    op.execute("""
        UPDATE messages
        SET message = (message::jsonb->0)::json
        WHERE message IS NOT NULL
        AND jsonb_typeof(message::jsonb) = 'array'
        AND jsonb_array_length(message::jsonb) > 0
    """)
