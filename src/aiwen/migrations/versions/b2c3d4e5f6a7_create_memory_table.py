"""create memory table with pgvector

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-01-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Enable pgvector extension (requires pgvector installed on PostgreSQL server)
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')

    # Create memory table
    op.create_table(
        'memory',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('app_id', postgresql.UUID(as_uuid=True), nullable=False, comment='关联的应用ID'),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False, comment='关联的用户ID'),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), nullable=True, comment='关联的对话ID'),
        sa.Column('memory_type', sa.String(50), nullable=False, server_default='history', comment='记忆类型: history, tool, knowledge'),
        sa.Column('content', sa.Text(), nullable=False, comment='记忆内容'),
        sa.Column('summary', sa.Text(), nullable=True, comment='记忆摘要'),
        sa.Column('metadata', postgresql.JSONB(), nullable=True, comment='额外元数据'),
        sa.Column('source', sa.String(255), nullable=True, comment='记忆来源'),
        sa.Column('importance', sa.Integer(), nullable=True, server_default='0', comment='重要性评分 0-100'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, comment='更新时间'),
    )

    # Add vector column using raw SQL (pgvector type)
    op.execute('ALTER TABLE memory ADD COLUMN embedding vector(1536)')
    op.execute("COMMENT ON COLUMN memory.embedding IS '向量嵌入'")

    # Create indexes
    op.create_index('ix_memory_app_id', 'memory', ['app_id'])
    op.create_index('ix_memory_user_id', 'memory', ['user_id'])
    op.create_index('ix_memory_type', 'memory', ['memory_type'])
    op.create_index('ix_memory_conversation_id', 'memory', ['conversation_id'])

    # Create HNSW vector index for fast similarity search
    op.execute('''
        CREATE INDEX ix_memory_embedding_hnsw ON memory
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    ''')


def downgrade() -> None:
    """Downgrade schema."""
    # Drop indexes
    op.drop_index('ix_memory_embedding_hnsw', table_name='memory')
    op.drop_index('ix_memory_conversation_id', table_name='memory')
    op.drop_index('ix_memory_type', table_name='memory')
    op.drop_index('ix_memory_user_id', table_name='memory')
    op.drop_index('ix_memory_app_id', table_name='memory')

    # Drop table
    op.drop_table('memory')

    # Note: Not dropping the vector extension as it may be used by other tables
