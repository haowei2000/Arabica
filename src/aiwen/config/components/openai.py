# config/components/openai.py

from pydantic import BaseModel, Field


class OpenAIConfig(BaseModel):
    """OpenAI API configuration for Tongyi and other OpenAI-compatible providers."""

    api_key: str = Field(default="", description="OpenAI API key")
    base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="OpenAI API base URL"
    )
    model: str = Field(default="qwen-plus", description="Default model name")
