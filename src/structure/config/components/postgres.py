# config/components/postgres.py
from urllib.parse import quote_plus

from pydantic import BaseModel, Field, PositiveInt


class PostgresConfig(BaseModel):
    """Postgres sub-configuration. Uses environment variable aliases
    (e.g. PG_DB_HOST) so values can be provided from a project .env file.
    """

    host: str = Field(
        ...,
        description="PostgreSQL host",
    )
    port: PositiveInt = Field(
        ...,
        description="PostgreSQL port",
    )
    username: str = Field(
        default="postgres",
        description="PostgreSQL user",
    )
    password: str = Field(
        default="123456",
        description="PostgreSQL password",
    )

    structure_dbname: str = Field(
        default="structure",
        description="Structure DB",
    )

    driver: str = Field(
        default="postgresql+asyncpg",  # 异步
        description="SQLAlchemy URI scheme",
    )

    @property
    def structure_sqlalchemy_bind(self) -> dict[str, str]:
        """Return a dict usable as SQLALCHEMY_BINDS entry for the 'structure' bind."""
        return {
            "structure": (
                f"{self.driver}://"
                f"{quote_plus(self.username)}:{quote_plus(self.password)}@"
                f"{self.host}:{self.port}/{self.structure_dbname}"
            )
        }
