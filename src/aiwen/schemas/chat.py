from pydantic import BaseModel


class StartTaskResult(BaseModel):
    """启动任务的返回结果"""

    task_id: str
    conversation_id: str
