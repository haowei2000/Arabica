from pydantic import BaseModel, Field


class ChatLLM(BaseModel):
    """Schema for model configuration."""

    name: str = Field(..., description="ChatLLM name")
    provider: str = Field(..., description="ChatLLM provider")