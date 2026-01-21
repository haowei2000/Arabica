from pydantic import BaseModel

from aiwen.schemas.agents.input import TextInput


class TaskPayload(BaseModel):
    """任务负载数据模型"""

    conversation_id: str
    message_id: str
    input: TextInput
