from uuid import UUID

from pydantic import BaseModel, Field


class TextInput(BaseModel):
    """
    Chat input input schema.

    Note: Agent and App are merged into a single concept.
    The agent_id (app_id) can be passed in the text_message or via the URL path.
    """

    query: str = Field(..., description="User query/input input")
    conversation_id: UUID | None = Field(
        default=None, description="Existing conversation ID (optional)"
    )
    app_id: UUID | None = Field(
        default=None, description="Agent/App ID (optional, may be in URL path)"
    )

    # Optional metadata for conversation creation
    conversation_name: str | None = Field(None, description="Name for new conversation")
    from_source: str = Field(default="api", description="Source of the input")
    from_account_id: UUID | None = Field(None, description="Account ID")

    def model_dump(self, **kwargs):
        """Pydantic V2 推荐方式 - returns all fields"""
        return super().model_dump(**kwargs)

    # 或者保持兼容 V1
    def dict(self, **kwargs):
        return super().dict(**kwargs)
