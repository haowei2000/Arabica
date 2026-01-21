# config/components/postgres.py
from urllib.parse import quote_plus

from pydantic import BaseModel, Field, PositiveInt


class PostgresConfig(BaseModel):
    """Postgres sub-configuration. Uses environment variable aliases
    (e.g. PG_DB_HOST) so values can be provided from a project .env file.
    """
    host: str = Field(..., description="PostgreSQL host", )
    port: PositiveInt = Field(..., description="PostgreSQL port", )
    username: str = Field(default="postgres", description="PostgreSQL user", )
    password: str = Field(default="123456", description="PostgreSQL password", )
    dify_dbname: str = Field(default="dify", description="Dify DB", )
    aiwen_dbname: str = Field(default="aiwen", description="Aiwen DB", )
    langgraph_dbname: str = Field(
        default="langgraph", description="LangGraph DB", )
    driver: str = Field(
        default="postgresql+asyncpg",  # 异步
        description="SQLAlchemy URI scheme", )

    @property
    def dify_sqlalchemy_bind(self) -> dict[str, str]:
        """Connection URL for the Dify/default database."""
        return {"dify":
            (
                f"{self.driver}://"
                f"{quote_plus(self.username)}:{quote_plus(self.password)}@"
                f"{self.host}:{self.port}/{self.dify_dbname}"
            )
        }

    @property
    def aiwen_sqlalchemy_bind(self) -> dict[str, str]:
        """Return a dict usable as SQLALCHEMY_BINDS entry for the 'aiwen' bind."""
        return {
            "aiwen": (
                f"{self.driver}://"
                f"{quote_plus(self.username)}:{quote_plus(self.password)}@"
                f"{self.host}:{self.port}/{self.aiwen_dbname}"
            )
        }

    @property
    def langgraph_sqlalchemy_bind(self) -> dict[str, str]:
        """Return a dict usable as SQLALCHEMY_BINDS entry for the 'langgraph' bind."""
        return {
            "langgraph": (
                f"{self.driver}://"
                f"{quote_plus(self.username)}:{quote_plus(self.password)}@"
                f"{self.host}:{self.port}/{self.langgraph_dbname}"
            )
        }
