"""Baseline schema (squashed migrations).

This revision replaces the historical migration chain for faster installs.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260301_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all tables for the current schema."""
    # Import models to register all tables on the metadata.
    from structure.extensions.database import get_base
    import structure.models

    bind = op.get_bind()
    get_base("structure").metadata.create_all(bind=bind)


def downgrade() -> None:
    """Drop all tables for the current schema."""
    from structure.extensions.database import get_base
    import structure.models

    bind = op.get_bind()
    get_base("structure").metadata.drop_all(bind=bind)
