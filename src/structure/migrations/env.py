import asyncio
from logging.config import fileConfig
import os
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool

from structure.config.factory import get_settings
from structure.core.bootstrap import bootstrap_alembic
from structure.extensions.database import get_base

os.environ.setdefault("ENV", "development")

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# 在模块级别运行异步函数
async def run_bootstrap():
    return await bootstrap_alembic()


# 运行异步引导程序
asyncio.run(run_bootstrap())

settings = get_settings()
config = context.config
# Get the structure database URL from settings
db_urls = settings.postgres.structure_sqlalchemy_bind
if db_urls:
    # Convert async URL to sync URL for Alembic
    async_url = list(db_urls.values())[0]  # noqa: RUF015
    # Or replace with psycopg2 specifically
    db_url = async_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
else:
    raise ValueError("No database URL found")

# Set the database URL
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)
    print(
        f"Using database URL: {db_url.split(':')[0]}:******@{db_url.split('@')[1] if '@' in db_url else db_url}"
    )

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Get the metadata from our structure database
target_metadata = get_base("structure").metadata


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
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
