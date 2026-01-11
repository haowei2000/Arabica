#!/usr/bin/env python3
"""
Task Consumer - 任务消费者

功能描述:
    从 Redis Streams 消费任务事件，支持 SSE（Server-Sent Events）流式响应。
    提供异步生成器接口，用于订阅任务执行过程中的实时事件。

作者: Claude
创建日期: 2025/12/30
版本: v1.0.0



依赖模块:
    - redis.asyncio: Redis 异步客户端
    - task_utils: 消息编码/解码工具
"""
import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

import redis.asyncio as aioredis

from .task_utils import decode_message_field, decode_message_id, parse_json_field

logger = logging.getLogger(__name__)


class TaskConsumer:
    """
    任务消费者

    从 Redis Stream 读取任务执行事件，支持流式消费模式。
    主要用于 SSE 场景，将 Agent 执行过程中的中间结果实时推送给前端。

    使用示例:
        ```python
        from aiwen.workers.task_consumer import TaskConsumer
        from aiwen.middleware.cache_middleware import get_redis_client

        redis_client = get_redis_client(is_async=True)
        consumer = TaskConsumer(redis_client)

        # 消费任务事件流
        async for event_data in consumer.consume_task_events(
            task_id="123",
            task_type="chat"
        ):
            print(f"Received event: {event_data}")
        ```
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        stream_prefix: str = "agent:task",
        read_count: int = 10,
        read_block_ms: int = 1000
    ):
        """
        初始化任务消费者

        Args:
            redis_client: Redis 异步客户端
            stream_prefix: Stream 名称前缀
            read_count: 每次读取的最大消息数
            read_block_ms: 读取阻塞时间（毫秒）
        """
        self.redis = redis_client
        self.stream_prefix = stream_prefix
        self.read_count = read_count
        self.read_block_ms = read_block_ms

    def _build_stream_name(self, task_type: str, task_id: str) -> str:
        """
        构建 Stream 名称

        Args:
            task_type: 任务类型
            task_id: 任务 ID

        Returns:
            完整的 Stream 名称
        """
        return f"{self.stream_prefix}:{task_type}:{task_id}:events"

    async def consume_task_events(
        self,
        task_id: str,
        task_type: str,
        last_id: str = "0-0",
        timeout_seconds: float | None = None
    ) -> AsyncGenerator[Any, None]:
        """
        消费任务事件流

        从 Redis Stream 读取任务执行事件，并以异步生成器形式返回。
        支持三种事件类型:
            - chunk: 中间结果事件（返回 payload）
            - success: 任务成功完成（生成器正常结束）
            - failed: 任务失败（抛出异常）

        Args:
            task_id: 任务 ID
            task_type: 任务类型
            last_id: 开始读取的消息 ID，默认从头开始
            timeout_seconds: 超时时间（秒），None 表示无限等待

        Yields:
            任务事件数据（通常为字典）

        Raises:
            Exception: 任务失败时抛出包含错误信息的异常
            asyncio.TimeoutError: 超时时抛出
        """
        stream_name = self._build_stream_name(task_type, task_id)
        start_time = asyncio.get_event_loop().time()

        logger.info(
            f"Starting to consume events for task {task_id} "
            f"from stream {stream_name}"
        )

        try:
            while True:
                # Check timeout
                if timeout_seconds is not None:
                    elapsed = asyncio.get_event_loop().time() - start_time
                    if elapsed >= timeout_seconds:
                        logger.warning(
                            f"Timeout after {elapsed:.2f}s consuming "
                            f"task {task_id}"
                        )
                        raise TimeoutError(
                            f"Task consumption timeout after {timeout_seconds}s"
                        )

                # Read from stream
                try:
                    messages = await self.redis.xread(
                        streams={stream_name: last_id},
                        count=self.read_count,
                        block=self.read_block_ms
                    )
                except TimeoutError:
                    # xread timeout, continue loop
                    continue
                except Exception as e:
                    logger.error(
                        f"Error reading from stream {stream_name}: {e}",
                        exc_info=True
                    )
                    raise

                # No messages received
                if not messages:
                    await asyncio.sleep(0.01)
                    continue

                # Process messages
                for stream_key, msgs in messages:
                    for message_id, data in msgs:
                        # Update last_id for next iteration
                        last_id = decode_message_id(message_id)

                        # Extract event type and payload
                        event_type = decode_message_field(data, "event")
                        payload = parse_json_field(data, "payload")

                        logger.debug(
                            f"Received event '{event_type}' for task {task_id}: "
                            f"{payload}"
                        )

                        # Handle different event types
                        if event_type == "chunk":
                            # Intermediate result
                            yield payload

                        elif event_type == "success":
                            # Task completed successfully
                            logger.info(
                                f"Task {task_id} completed successfully"
                            )
                            return

                        elif event_type == "failed":
                            # Task failed
                            error_msg = payload if isinstance(payload, str) else str(payload)
                            logger.error(
                                f"Task {task_id} failed: {error_msg}"
                            )
                            raise Exception(error_msg)

                        else:
                            # Unknown event type, log and continue
                            logger.warning(
                                f"Unknown event type '{event_type}' for "
                                f"task {task_id}"
                            )

        except asyncio.CancelledError:
            logger.info(f"Task {task_id} consumption cancelled")
            raise
        except Exception as e:
            logger.error(
                f"Error consuming task {task_id}: {e}",
                exc_info=True
            )
            raise

    async def get_stream_length(
        self,
        task_type: str,
        task_id: str
    ) -> int:
        """
        获取任务事件流的长度

        Args:
            task_type: 任务类型
            task_id: 任务 ID

        Returns:
            Stream 中的消息数量
        """
        stream_name = self._build_stream_name(task_type, task_id)
        try:
            length = await self.redis.xlen(stream_name)
            return length
        except Exception as e:
            logger.error(
                f"Error getting stream length for {stream_name}: {e}"
            )
            return 0

    async def stream_exists(
        self,
        task_type: str,
        task_id: str
    ) -> bool:
        """
        检查任务事件流是否存在

        Args:
            task_type: 任务类型
            task_id: 任务 ID

        Returns:
            流是否存在
        """
        stream_name = self._build_stream_name(task_type, task_id)
        try:
            exists = await self.redis.exists(stream_name)
            return bool(exists)
        except Exception as e:
            logger.error(
                f"Error checking stream existence for {stream_name}: {e}"
            )
            return False

    async def delete_stream(
        self,
        task_type: str,
        task_id: str
    ) -> bool:
        """
        删除任务事件流

        Args:
            task_type: 任务类型
            task_id: 任务 ID

        Returns:
            是否成功删除
        """
        stream_name = self._build_stream_name(task_type, task_id)
        try:
            deleted = await self.redis.delete(stream_name)
            logger.info(f"Deleted stream {stream_name}")
            return bool(deleted)
        except Exception as e:
            logger.error(
                f"Error deleting stream {stream_name}: {e}",
                exc_info=True
            )
            return False
