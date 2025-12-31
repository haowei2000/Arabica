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

公司名称: 艾普工华(武汉)有限责任公司
版权信息: © 2025 艾普工华(武汉)有限责任公司. 保留所有权利.

依赖模块:
    - redis.asyncio: Redis 异步客户端
"""
import json
import logging
from typing import Any, Dict, Optional
from uuid import UUID

import redis.asyncio as redis_async

logger = logging.getLogger(__name__)


class TaskProducer:
    """
    任务生产者

    使用 XADD 将任务发布到 Redis Stream，供 Worker 消费。

    使用示例:
        ```python
        from aiwen.workers.task_producer import TaskProducer
        from aiwen.middleware.cache_middleware import get_redis_client

        redis_client = get_redis_client(is_async=True)
        producer = TaskProducer(redis_client)

        # 发布任务
        message_id = await producer.publish_task(
            task_id=task.id,
            app_id=app.id,
            payload={"query": "Hello"}
        )
        ```
    """

    # 与 AgentWorker 保持一致
    STREAM_NAME = "agent_tasks"

    def __init__(self, redis_client: redis_async.Redis):
        """
        初始化任务生产者

        Args:
            redis_client: Redis 异步客户端
        """
        self.redis_client = redis_client

    async def publish_task(
        self,
        task_id: UUID,
        payload: dict[str, Any] | None = None
    ) -> str:
        """
        发布任务到 Redis Stream

        Args:
            task_id: 任务 ID
            payload: 任务载荷数据

        Returns:
            消息 ID (Redis Stream message ID)

        Raises:
            Exception: 发布失败时抛出异常
        """
        try:
            # 准备消息数据
            message_data = {
                "task_id": str(task_id),
                "payload": json.dumps(payload or {})
            }

            # XADD 发布到 Stream
            task_message_id = await self.redis_client.xadd(
                name=self.STREAM_NAME,
                fields=message_data,
                maxlen=10000,  # 限制 Stream 最大长度（可选）
                approximate=True  # 使用近似修剪以提高性能
            )

            logger.info(
                f"Published task {task_id} to stream (message: {task_message_id})"
            )

            return task_message_id

        except Exception as e:
            logger.error(
                f"Error publishing task {task_id} to stream: {e}",
                exc_info=True
            )
            raise

    async def publish_scheduled_task(
        self,
        task_id: UUID,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None
    ) -> str:
        """
        发布调度任务到 Redis Stream

        与普通任务类似，但可以附加调度元数据。

        Args:
            task_id: 任务 ID
            payload: 任务载荷数据
            metadata: 调度元数据（如调度时间、触发器等）

        Returns:
            消息 ID
        """
        try:
            # 准备消息数据
            message_data = {
                "task_id": str(task_id),
                "payload": json.dumps(payload or {}),
                "scheduled": "true"
            }

            # 添加元数据
            if metadata:
                message_data["metadata"] = json.dumps(metadata)

            # XADD 发布到 Stream
            task_message_id = await self.redis_client.xadd(
                name=self.STREAM_NAME,
                fields=message_data,
                maxlen=10000,
                approximate=True
            )

            logger.info(
                f"Published scheduled task {task_id} to stream "
                f"(message: {task_message_id})"
            )

            return task_message_id

        except Exception as e:
            logger.error(
                f"Error publishing scheduled task {task_id}: {e}",
                exc_info=True
            )
            raise

    async def get_stream_length(self) -> int:
        """
        获取 Stream 长度

        Returns:
            Stream 中的消息数量
        """
        try:
            length = await self.redis_client.xlen(self.STREAM_NAME)
            return length
        except Exception as e:
            logger.error(f"Error getting stream length: {e}")
            return 0

    async def get_stream_info(self) -> dict[str, Any] | None:
        """
        获取 Stream 信息

        Returns:
            Stream 信息字典或 None
        """
        try:
            info = await self.redis_client.xinfo_stream(self.STREAM_NAME)
            return info
        except Exception as e:
            logger.error(f"Error getting stream info: {e}")
            return None

    async def get_pending_count(
        self,
        consumer_group: str = "agent_workers"
    ) -> int:
        """
        获取待处理消息数量

        Args:
            consumer_group: 消费者组名称

        Returns:
            待处理消息数量
        """
        try:
            pending = await self.redis_client.xpending(
                self.STREAM_NAME,
                consumer_group
            )
            if pending:
                return pending['pending']
            return 0
        except Exception as e:
            logger.error(f"Error getting pending count: {e}")
            return 0


# 全局单例（可选）
_producer_instance: TaskProducer | None = None


def get_task_producer(redis_client: redis_async.Redis) -> TaskProducer:
    """
    获取任务生产者单例

    Args:
        redis_client: Redis 异步客户端

    Returns:
        TaskProducer 实例
    """
    global _producer_instance

    if _producer_instance is None:
        _producer_instance = TaskProducer(redis_client)

    return _producer_instance


# FastAPI 依赖注入示例函数
async def get_producer_dependency(
    redis_client: redis_async.Redis
) -> TaskProducer:
    """
    FastAPI 依赖注入函数

    使用示例:
        ```python
        from fastapi import Depends
        from aiwen.workers.task_producer import get_producer_dependency

        @app.post("/tasks")
        async def create_task(
            producer: TaskProducer = Depends(get_producer_dependency)
        ):
            message_id = await producer.publish_task(...)
            return {"message_id": message_id}
        ```

    Args:
        redis_client: Redis 客户端（通过依赖注入）

    Returns:
        TaskProducer 实例
    """
    return get_task_producer(redis_client)
