"""Initial database schema

Create all tables for the application.

Revision ID: 0001
Revises: 
Create Date: 2025-12-23 11:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all tables for the initial database schema."""

    # Create auth_tenant table
    op.create_table(
        'auth_tenant',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.String(500), nullable=True),
        sa.Column('is_active', sa.Boolean, server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('admin_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )

    # Create auth_user table
    op.create_table(
        'auth_user',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('username', sa.String(50), nullable=False),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('phone', sa.String(20), nullable=True),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role', sa.String(50), server_default='user', nullable=False),
        sa.Column('is_active', sa.Boolean, server_default='true', nullable=False),
        sa.Column('is_superuser', sa.Boolean, server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
        sa.UniqueConstraint('phone'),
        sa.UniqueConstraint('username'),
        sa.ForeignKeyConstraint(['tenant_id'], ['auth_tenant.id'], name='fk_user_tenant')
    )

    # Create app table
    op.create_table(
        'app',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('app_code', sa.String, nullable=False),
        sa.Column('app_type', sa.String, nullable=False),
        sa.Column('enabled', sa.Boolean, server_default='true', nullable=False),
        sa.Column('config', postgresql.JSONB, nullable=True),
        sa.Column('version', sa.Integer, server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('app_code')
    )

    # Create agent_template table
    op.create_table(
        'agent_template',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('template_code', sa.String, nullable=False),
        sa.Column('template_name', sa.String, nullable=False),
        sa.Column('enabled', sa.Boolean, server_default='true', nullable=False),
        sa.Column('config', postgresql.JSONB, nullable=True),
        sa.Column('version', sa.Integer, server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('template_code')
    )

    # Create agent_task table
    op.create_table(
        'agent_task',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('app_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('task_type', sa.String, nullable=True),
        sa.Column('status', sa.String, server_default='pending', nullable=False),
        sa.Column('payload', postgresql.JSONB, nullable=True),
        sa.Column('result', postgresql.JSONB, nullable=True),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('progress', sa.Integer, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['app_id'], ['app.id'], name='fk_agent_task_app', ondelete='CASCADE')
    )

    # Create conversations table
    op.create_table(
        'conversations',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('app_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('summary', sa.Text, nullable=True),
        sa.Column('status', sa.String(255), nullable=False),
        sa.Column('from_source', sa.String(255), nullable=False),
        sa.Column('account_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('dialogue_count', sa.Integer, server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column('is_deleted', sa.Boolean, server_default=text("false"), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # Create messages table
    op.create_table(
        'messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('app_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('summary', sa.Text, nullable=True),
        sa.Column('query', sa.Text, nullable=False),
        sa.Column('message', postgresql.JSON, nullable=False),
        sa.Column('answer', sa.Text, nullable=False),
        sa.Column('status', sa.String(255), server_default="'normal'", nullable=False),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('message_metadata', sa.Text, nullable=True),
        sa.Column('from_source', sa.String(255), nullable=False),
        sa.Column('from_account_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('from_end_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('workflow_run_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('app_mode', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint('id', name='message_pkey'),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name='fk_messages_conversation', ondelete='CASCADE')
    )

    # Create indexes for messages table
    op.create_index('message_app_id_idx', 'messages', ['app_id', 'created_at'])
    op.create_index('message_conversation_id_idx', 'messages', ['conversation_id'])
    op.create_index('message_account_idx', 'messages', ['app_id', 'from_source', 'from_account_id'])
    op.create_index('message_workflow_run_id_idx', 'messages', ['conversation_id', 'workflow_run_id'])
    op.create_index('message_created_at_idx', 'messages', ['created_at'])
    op.create_index('message_app_mode_idx', 'messages', ['app_mode'])
    op.create_index('message_end_user_idx', 'messages', ['app_id', 'from_source', 'from_end_user_id'])


def downgrade() -> None:
    """Drop all tables for the initial database schema."""

    # Drop indexes first
    op.drop_index('message_end_user_idx', table_name='messages')
    op.drop_index('message_app_mode_idx', table_name='messages')
    op.drop_index('message_created_at_idx', table_name='messages')
    op.drop_index('message_workflow_run_id_idx', table_name='messages')
    op.drop_index('message_account_idx', table_name='messages')
    op.drop_index('message_conversation_id_idx', table_name='messages')
    op.drop_index('message_app_id_idx', table_name='messages')

    # Drop tables in reverse order of creation to respect foreign key constraints
    op.drop_table('messages')
    op.drop_table('conversations')
    op.drop_table('agent_task')
    op.drop_table('agent_template')
    op.drop_table('app')
    op.drop_table('auth_user')
    op.drop_table('auth_tenant')
