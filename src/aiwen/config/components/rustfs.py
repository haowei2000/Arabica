"""rustfs configuration module."""

from pydantic import BaseModel, Field, PositiveInt


class RustfsConfig(BaseModel):
    """rustfs sub-configuration.

    Uses environment variable aliases (e.g. rustfs__HOST) so values
    can be provided from a project .env file.
    """

    host: str = Field(default="127.0.0.1", description="rustfs host")
    port: PositiveInt = Field(default=9000, description="rustfs port")
    access_key: str = Field(default="", description="rustfs access key")
    secret_key: str = Field(default="", description="rustfs secret key")
    secure: bool = Field(default=False, description="Use HTTPS")
    bucket: str = Field(default="knowledge", description="Default bucket name")

    @property
    def endpoint(self) -> str:
        """Get rustfs endpoint URL."""
        return f"{'https' if self.secure else 'http'}://{self.host}:{self.port}"
