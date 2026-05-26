# config/components/openai.py

from pydantic import BaseModel, Field


class OpenAIConfig(BaseModel):
    """OpenAI-compatible LLM API configuration."""

    api_key: str = Field(default="", description="OpenAI API key")
    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="OpenAI API base URL",
    )
    model: str = Field(default="gpt-4.1-mini", description="Default chat model name")
