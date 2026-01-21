import logging
from logging.config import fileConfig
import os
from pathlib import Path
import sys
from urllib.parse import quote_plus

from dotenv import load_dotenv

# Add the src directory to the path so we can import the app modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from alembic import context
from sqlalchemy import engine_from_config, pool

# Load environment variables from src/.env file
# Set default ENV if not provided
os.environ.setdefault("ENV", "development")

db_url = None
try:
    from aiwen.config.factory import get_settings

    settings = get_settings()
    # Get the aiwen database URL from settings
    db_urls = settings.postgres.aiwen_sqlalchemy_bind
    if db_urls:
        # Convert async URL to sync URL for Alembic
        async_url = list(db_urls.values())[0]
        # Or replace with psycopg2 specifically
        db_url = async_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
except Exception as e:
    print(f"Warning: Could not load settings from config factory: {e}")

# Fallback to environment variables if config loading fails
if not db_url:
    db_url = (
        f"postgresql+psycopg2://"
        f"{quote_plus(os.environ.get('POSTGRES__USERNAME', 'postgres'))}:{quote_plus(os.environ.get('POSTGRES__PASSWORD', '123456'))}@"
        f"{os.environ.get('POSTGRES__HOST', 'localhost')}:{os.environ.get('POSTGRES__PORT', '5432')}/{os.environ.get('POSTGRES__AIWEN_DBNAME', 'aiwen_agent')}"
    )

# Import our models after setting environment variables
from aiwen.extensions.database import get_base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Get the metadata from our aiwen database
target_metadata = get_base("aiwen").metadata

# Set the database URL
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)
    print(f"Using database URL: {db_url.split(':')[0]}:******@{db_url.split('@')[1] if '@' in db_url else db_url}")


# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Force synchronous pool class for Alembic
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
