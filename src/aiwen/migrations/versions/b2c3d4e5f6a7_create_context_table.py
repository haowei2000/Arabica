"""create context table with optional pgvector support

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-01-11 00:00:00.000000

"""
import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

logger = logging.getLogger(__name__)

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _check_pgvector_available(connection) -> bool:
    """Check if pgvector extension is available on the PostgreSQL server."""
    try:
        result = connection.execute(sa.text(
            "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'"
        ))
        return result.fetchone() is not None
    except Exception:
        return False


def upgrade() -> None:
    """Upgrade schema."""
    connection = op.get_bind()
    pgvector_available = _check_pgvector_available(connection)

    if pgvector_available:
        # Enable pgvector extension
        op.execute('CREATE EXTENSION IF NOT EXISTS vector')
        logger.info("pgvector extension enabled")
    else:
        logger.warning(
            "pgvector extension is not available on this PostgreSQL server. "
            "Vector embedding features will be disabled. "
            "Install pgvector to enable similarity search: https://github.com/pgvector/pgvector"
        )

    # Create context table
    op.create_table(
        'context',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('app_id', postgresql.UUID(as_uuid=True), nullable=False, comment='关联的应用ID'),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False, comment='关联的用户ID'),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), nullable=True, comment='关联的对话ID'),
        sa.Column('context_type', sa.String(50), nullable=False, server_default='history', comment='上下文类型: history, tool, knowledge'),
        sa.Column('content', sa.Text(), nullable=False, comment='上下文内容'),
        sa.Column('summary', sa.Text(), nullable=True, comment='上下文摘要'),
        sa.Column('metadata', postgresql.JSONB(), nullable=True, comment='额外元数据'),
        sa.Column('source', sa.String(255), nullable=True, comment='上下文来源'),
        sa.Column('importance', sa.Integer(), nullable=True, server_default='0', comment='重要性评分 0-100'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True, comment='更新时间'),
    )

    # Add vector column only if pgvector is available
    if pgvector_available:
        op.execute('ALTER TABLE context ADD COLUMN embedding vector(1536)')
        op.execute("COMMENT ON COLUMN context.embedding IS '向量嵌入'")

    # Create standard indexes
    op.create_index('ix_context_app_id', 'context', ['app_id'])
    op.create_index('ix_context_user_id', 'context', ['user_id'])
    op.create_index('ix_context_type', 'context', ['context_type'])
    op.create_index('ix_context_conversation_id', 'context', ['conversation_id'])

    # Create HNSW vector index only if pgvector is available
    if pgvector_available:
        op.execute('''
            CREATE INDEX ix_context_embedding_hnsw ON context
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        ''')


def downgrade() -> None:
    """Downgrade schema."""
    connection = op.get_bind()

    # Check if vector index exists before dropping
    result = connection.execute(sa.text(
        "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_context_embedding_hnsw'"
    ))
    if result.fetchone():
        op.drop_index('ix_context_embedding_hnsw', table_name='context')

    # Drop standard indexes
    op.drop_index('ix_context_conversation_id', table_name='context')
    op.drop_index('ix_context_type', table_name='context')
    op.drop_index('ix_context_user_id', table_name='context')
    op.drop_index('ix_context_app_id', table_name='context')

    # Drop table
    op.drop_table('context')

    # Note: Not dropping the vector extension as it may be used by other tables
