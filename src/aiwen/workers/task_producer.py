#!/usr/bin/env python3
"""
Task Producer - 任务生产者

功能描述:
    发布任务到 Redis Streams，供 Worker 消费处理。
    支持 FastAPI 路由和 Scheduler 调用。

作者: haowei
创建日期: 2025/12/24
最后修改: 2025/12/24 10:30
修改人员: haowei
版本: v1.0.0


依赖模块:
    - redis.asyncio: Redis 异步客户端
"""
import logging
from uuid import UUID

import redis.asyncio as redis_async

from aiwen.schemas.task.field import TaskField
from aiwen.schemas.task.payload import TaskPayload
from aiwen.workers.task_utils import build_stream_name_key

logger = logging.getLogger(__name__)


class AgentTaskProducer:
    """
    任务生产者

    使用 XADD 将任务发布到 Redis Stream，供 Worker 消费。

    使用示例:
        ```python
        from aiwen.workers.task_producer import AgentTaskProducer
        from aiwen.middleware.cache_middleware import get_redis_client

        redis_client = get_redis_client(is_async=True)
        producer = AgentTaskProducer(redis_client)

        # 发布任务
        message_id = await producer.publish_task(
            task_id=task.id,
            app_id=app.id,
            text_message={"query": "Hello"}
        )
        ```
    """

    def __init__(self, redis_client: redis_async.Redis):
        """
        初始化任务生产者

        Args:
            redis_client: Redis 异步客户端
        """
        self.redis_client = redis_client
        self.task_name = "agent"

    async def publish_task(
            self,
            task_id: UUID,
            payload: TaskPayload | None = None
    ) -> str:
        """
        发布任务到 Redis Stream

        Args:
            task_id: 任务 ID
            payload: 任务载荷数据

        Returns:
            消息 ID (Redis Stream input ID)

        Raises:
            Exception: 发布失败时抛出异常
        """
        try:

            field = TaskField(
                task_id=str(task_id),
                payload=payload,
                status="pending"
            )
            task_message_id = await self.redis_client.xadd(
                name=build_stream_name_key(str(task_id), self.task_name),
                fields=field.model_dump(),
                maxlen=10000,  # 限制 Stream 最大长度（可选）
                approximate=True  # 使用近似修剪以提高性能
            )
            logger.info(
                f"Published task {task_id} to stream (input: {task_message_id})"
            )
            return task_message_id
        except Exception as e:
            logger.error(
                f"Error publishing task {task_id} to stream: {e}",
                exc_info=True
            )
            raise
