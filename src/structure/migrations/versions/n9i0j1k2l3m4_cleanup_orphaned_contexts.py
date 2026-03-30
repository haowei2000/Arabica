"""cleanup orphaned context records

Removes Context rows whose parent resource (document/skill/knowledge/tool) no
longer exists or has been soft-deleted.  This is a one-time data-migration to
clear records that accumulated before the cascade-delete fixes were applied.

Revision ID: n9i0j1k2l3m4
Revises: m8h9i0j1k2l3
Create Date: 2026-03-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "n9i0j1k2l3m4"
down_revision: Union[str, None] = "m8h9i0j1k2l3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Document chunks whose document has been soft-deleted
    #    (source_id points to a document with is_deleted = true)
    op.execute(
        """
        DELETE FROM context
        WHERE context_type = 'chunk'
          AND source_id IN (
              SELECT id FROM document WHERE is_deleted = true
          )
        """
    )

    # 2. Document chunks whose knowledge base no longer exists
    #    (knowledge_id stored in meta no longer in the knowledge table)
    op.execute(
        """
        DELETE FROM context
        WHERE context_type = 'chunk'
          AND (meta->>'knowledge_id') IS NOT NULL
          AND (meta->>'knowledge_id')::uuid NOT IN (SELECT id FROM knowledge)
        """
    )

    # 3. Skill contexts whose skill no longer exists
    op.execute(
        """
        DELETE FROM context
        WHERE context_type = 'skill'
          AND source_id IS NOT NULL
          AND source_id NOT IN (SELECT id FROM skill)
        """
    )

    # 4. Knowledge contexts whose knowledge base no longer exists
    op.execute(
        """
        DELETE FROM context
        WHERE context_type = 'knowledge'
          AND source_id IS NOT NULL
          AND source_id NOT IN (SELECT id FROM knowledge)
        """
    )

    # 5. Tool contexts whose tool no longer exists
    op.execute(
        """
        DELETE FROM context
        WHERE context_type = 'tool'
          AND source_id IS NOT NULL
          AND source_id NOT IN (SELECT id FROM tool)
        """
    )


    # 6. Backfill leading '/' on tool and skill paths that were stored without it
    op.execute(
        """
        UPDATE context
           SET path = '/' || path
         WHERE context_type IN ('tool', 'skill')
           AND path IS NOT NULL
           AND path NOT LIKE '/%'
        """
    )


def downgrade() -> None:
    # Data cleanup is irreversible — deleted rows cannot be restored.
    pass
