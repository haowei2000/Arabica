from pydantic import BaseModel, Field


class ChatLLM(BaseModel):
    """Legacy non-runtime model metadata.

    Runtime LLM API access is resolved only from OPENAI__API_KEY,
    OPENAI__BASE_URL, and OPENAI__MODEL.
    """

    name: str = Field(..., description="ChatLLM name")
    provider: str = Field(..., description="ChatLLM provider")
