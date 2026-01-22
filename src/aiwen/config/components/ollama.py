# config/components/ollama.py

from pydantic import BaseModel, Field, PositiveInt


class OllamaConfig(BaseModel):
    """ """

    host: str = Field(default="127.0.0.1", description="Ollama host")
    port: PositiveInt = Field(default="11434", description="ollama port")
    protocol: str = Field(default="http", description="Ollama protocol")

    @property
    def base_url(self) -> str:
        """Connection URL for the Ollama server."""
        return f"{self.protocol}://{self.host}:{self.port}"
