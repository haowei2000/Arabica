from pydantic import BaseModel

from aiwen.schemas.task.payload import TaskPayload


class TaskField(BaseModel):
    """任务字段数据模型"""

    task_id: str
    payload: TaskPayload | None = None
    status: str
