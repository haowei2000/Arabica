"""add_foreign_key_to_messages_conversation_id

Revision ID: dd983e0a2018
Revises: da790b82e86b
Create Date: 2025-12-23 15:20:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'dd983e0a2018'
down_revision = 'da790b82e86b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add foreign key constraint from messages.conversation_id to conversations.id"""
    # Add foreign key constraint
    op.create_foreign_key(
        'fk_messages_conversation_id',  # constraint name
        'messages',  # source table
        'conversations',  # target table
        ['conversation_id'],  # source column
        ['id'],  # target column
        ondelete='CASCADE'  # delete messages when conversation is deleted
    )


def downgrade() -> None:
    """Remove foreign key constraint"""
    op.drop_constraint('fk_messages_conversation_id', 'messages', type_='foreignkey')
