"""normalize context_type casing to lowercase

Revision ID: l7g8h9i0j1k2
Revises: k6f7g8h9i0j1
Create Date: 2026-03-27 00:00:00.000000

"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "l7g8h9i0j1k2"
down_revision: str | None = "20d3c9dbd15c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Lowercase any uppercase context_type values (e.g. "CHUNK" → "chunk", "SKILL" → "skill").
    # Also updates "run_events" if it exists (was not in enum but used as raw string).
    op.execute(
        "UPDATE context SET context_type = LOWER(context_type) "
        "WHERE context_type != LOWER(context_type)"
    )


def downgrade() -> None:
    # Restore legacy uppercase values for CHUNK and SKILL.
    op.execute("UPDATE context SET context_type = 'CHUNK' WHERE context_type = 'chunk'")
    op.execute("UPDATE context SET context_type = 'SKILL' WHERE context_type = 'skill'")
