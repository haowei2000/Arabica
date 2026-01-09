#!/usr/bin/env python3
"""
Task Queue Service - 任务队列服务

功能描述:
    提供统一的任务队列服务接口，整合 TaskProducer 和 TaskConsumer。
    为了向后兼容，保留原有 API，但内部委托给新的模块化组件。

作者: Claude
创建日期: 2025/12/30
版本: v2.0.0



依赖模块:
    - task_consumer: 任务消费者
    - task_utils: 编码/解码工具
"""
import asyncio
from collections.abc import AsyncGenerator, Callable
import json
import logging
from typing import Any, Dict, Optional

import redis.asyncio as aioredis

from .task_consumer import TaskConsumer
from .task_utils import encode_message_data

logger = logging.getLogger(__name__)


class TaskQueueService:
    """
    任务队列服务

    整合任务发布和消费功能的统一服务接口。
    该类主要用于向后兼容，实际功能委托给 TaskConsumer。

    使用示例:
        ```python
        from aiwen.workers.task_queue import TaskQueueService
        from aiwen.middleware.cache_middleware import get_redis_client

        redis_client = get_redis_client(is_async=True)
        queue_service = TaskQueueService(redis_client)

        # 发布任务
        await queue_service.publish_task(
            task_id="123",
            task_type="chat",
            payload={"query": "Hello"}
        )

        # 消费任务事件
        async for event in queue_service.consume_stream(
            task_id="123",
            task_type="chat"
        ):
            print(event)
        ```
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        stream_prefix: str = "agent:task",
        default_group: str = "task_group"
    ):
        """
        初始化任务队列服务

        Args:
            redis_client: Redis 异步客户端
            stream_prefix: Stream 名称前缀
            default_group: 默认消费者组名称
        """
        self.redis = redis_client
        self.stream_prefix = stream_prefix
        self.default_group = default_group
        self.consumers: dict[str, Callable] = {}  # task_type -> handler

        # Initialize consumer delegate
        self._consumer = TaskConsumer(
            redis_client=redis_client,
            stream_prefix=stream_prefix
        )

    # ----------------- Producer -----------------
    async def publish_task(
        self,
        task_id: str,
        task_type: str,
        payload: dict,
        event: str = "chunk"
    ) -> str:
        """
        发布任务到 Redis Stream

        Args:
            task_id: 任务 ID
            task_type: 任务类型
            payload: 任务载荷数据
            event: 事件类型（chunk/success/failed）

        Returns:
            消息 ID

        Raises:
            Exception: 发布失败时抛出异常
        """
        stream = f"{self.stream_prefix}:{task_type}:{task_id}:events"
        await self._ensure_group(stream)

        # Encode message data
        message_data = encode_message_data({
            "task_id": task_id,
            "task_type": task_type,
            "payload": payload,
            "event": event
        })

        try:
            message_id = await self.redis.xadd(stream, message_data)
            logger.debug(
                f"Published {event} event for task {task_id} "
                f"to {stream} (message_id: {message_id})"
            )
            return message_id
        except Exception as e:
            logger.error(
                f"Error publishing task {task_id} to {stream}: {e}",
                exc_info=True
            )
            raise

    # ----------------- Register Consumer -----------------
    def register_consumer(self, task_type: str, handler: Callable) -> None:
        """
        注册任务类型的处理器

        Args:
            task_type: 任务类型
            handler: 处理函数
        """
        self.consumers[task_type] = handler
        logger.info(f"Registered consumer handler for task type: {task_type}")

    # ----------------- Stream SSE Generator -----------------
    async def consume_stream(
        self,
        task_id: str,
        task_type: str,
        last_id: str = "0-0",
        timeout_seconds: float | None = None
    ) -> AsyncGenerator[Any, None]:
        """
        消费任务事件流

        委托给 TaskConsumer 实现，提供向后兼容的 API。

        Args:
            task_id: 任务 ID
            task_type: 任务类型
            last_id: 开始读取的消息 ID
            timeout_seconds: 超时时间（秒）

        Yields:
            任务事件数据

        Raises:
            Exception: 任务失败或其他错误
        """
        async for event_data in self._consumer.consume_task_events(
            task_id=task_id,
            task_type=task_type,
            last_id=last_id,
            timeout_seconds=timeout_seconds
        ):
            yield event_data

    # ----------------- Utility Methods -----------------
    async def get_stream_length(
        self,
        task_id: str,
        task_type: str
    ) -> int:
        """
        获取任务事件流的长度

        Args:
            task_id: 任务 ID
            task_type: 任务类型

        Returns:
            Stream 中的消息数量
        """
        return await self._consumer.get_stream_length(task_type, task_id)

    async def stream_exists(
        self,
        task_id: str,
        task_type: str
    ) -> bool:
        """
        检查任务事件流是否存在

        Args:
            task_id: 任务 ID
            task_type: 任务类型

        Returns:
            流是否存在
        """
        return await self._consumer.stream_exists(task_type, task_id)

    async def delete_stream(
        self,
        task_id: str,
        task_type: str
    ) -> bool:
        """
        删除任务事件流

        Args:
            task_id: 任务 ID
            task_type: 任务类型

        Returns:
            是否成功删除
        """
        return await self._consumer.delete_stream(task_type, task_id)

    # ----------------- Internal -----------------
    async def _ensure_group(self, stream: str) -> None:
        """
        确保 Stream 的消费者组存在

        Args:
            stream: Stream 名称

        Raises:
            Exception: 创建消费者组失败
        """
        try:
            await self.redis.xgroup_create(
                stream,
                self.default_group,
                id="0",
                mkstream=True
            )
            logger.debug(f"Created consumer group '{self.default_group}' for stream {stream}")
        except aioredis.exceptions.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                logger.error(f"Error creating consumer group for {stream}: {e}")
                raise
