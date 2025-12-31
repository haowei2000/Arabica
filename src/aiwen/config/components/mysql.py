# config/components/mysql.py
from urllib.parse import quote_plus

from pydantic import BaseModel, Field, PositiveInt


class MysqlConfig(BaseModel):
    """MySQL sub-configuration. Uses environment variable aliases
    (e.g. MYSQL__HOST) so values can be provided from a project .env file.
    """
    host: str = Field(..., description="MySQL host")
    port: PositiveInt = Field(..., description="MySQL port")
    username: str = Field(default="root", description="MySQL user")
    password: str = Field(default="password", description="MySQL password")
    dbname: str = Field(default="myapp", description="MySQL DB")
    driver: str = Field(
        default="mysql+asyncmy",  # 异步
        description="SQLAlchemy URI scheme")


    @property
    def mes_sqlalchemy_bind(self) -> dict[str, str]:
        """Connection URL for the MES/default database."""
        return {"mes":
            (
                f"{self.driver}://"
                f"{quote_plus(self.username)}:{quote_plus(self.password)}@"
                f"{self.host}:{self.port}/{self.dbname}"
            )
        }
