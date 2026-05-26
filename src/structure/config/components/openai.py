# config/components/openai.py

from pydantic import BaseModel, Field


class OpenAIConfig(BaseModel):
    """OpenAI-compatible LLM API configuration.

    The runtime LLM contract is intentionally limited to these three
    environment variables:
    - ``OPENAI__API_KEY``
    - ``OPENAI__BASE_URL``
    - ``OPENAI__MODEL``
    """

    api_key: str = Field(default="", description="OpenAI API key")
    base_url: str = Field(default="", description="OpenAI-compatible API base URL")
    model: str = Field(default="", description="Default chat model name")
