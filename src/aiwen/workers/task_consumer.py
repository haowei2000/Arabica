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
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

import redis.asyncio as aioredis
from pydantic import BaseModel

from .task_utils import (
    decode_data_field,
    parse_json_field,
    build_stream_name_key,
    decode_bytes,
)

logger = logging.getLogger(__name__)


class EventAction(Enum):
    """事件处理动作"""

    YIELD = auto()
    RETURN = auto()
    RAISE = auto()
    SKIP = auto()


@dataclass
class EventResult:
    """事件处理结果"""

    action: EventAction
    data: Any = None


def _handle_event(event_type: str, event_data: Any, task_id: str) -> EventResult:
    """处理事件，返回 EventResult"""
    logger.debug(f"Received event '{event_type}' for task {task_id}: {event_data}")
    if isinstance(event_data, BaseModel):
        event_data = event_data.model_dump_json()
    match event_type:
        case "chunk":
            return EventResult(EventAction.YIELD, event_data)
        case "success":
            logger.info(f"Task {task_id} completed successfully")
            return EventResult(EventAction.RETURN)
        case "failed":
            error_msg = event_data if isinstance(event_data, str) else str(event_data)
            logger.error(f"Task {task_id} failed: {error_msg}")
            return EventResult(EventAction.RAISE, Exception(error_msg))
        case _:
            logger.warning(f"Unknown event type '{event_type}' for task {task_id}")
            return EventResult(EventAction.SKIP)


class AgentTaskConsumer:
    """
    任务消费者

    从 Redis Stream 读取任务执行事件，支持流式消费模式。
    主要用于 SSE 场景，将 Agent 执行过程中的中间结果实时推送给前端。

    使用示例:
        ```python
        from aiwen.workers.task_consumer import AgentTaskConsumer
        from aiwen.middleware.cache_middleware import get_redis_client

        redis_client = get_redis_client(is_async=True)
        consumer = AgentTaskConsumer(redis_client)

        # 消费任务事件流
        async for event_data in consumer.get_task_events(
            task_id="123",
            task_type="chat"
        ):
            print(f"Received event: {event_data}")
        ```
    """

    def __init__(
            self,
            redis_client: aioredis.Redis,
            read_count: int = 10,
            read_block_ms: int = 1000,
    ):
        """
        初始化任务消费者

        Args:
            redis_client: Redis 异步客户端
            read_count: 每次读取的最大消息数
            read_block_ms: 读取阻塞时间（毫秒）
        """
        self.redis = redis_client
        self.read_count = read_count
        self.read_block_ms = read_block_ms
        self.task_name = "agent"

    @staticmethod
    def _check_timeout(
            start_time: float, timeout_seconds: float | None, task_id: str
    ) -> None:
        """检查是否超时，超时则抛出异常"""
        if timeout_seconds is None:
            return
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed >= timeout_seconds:
            logger.warning(f"Timeout after {elapsed:.2f}s consuming task {task_id}")
            raise TimeoutError(f"Task consumption timeout after {timeout_seconds}s")

    async def _read_messages(self, stream_name: str, last_id: str) -> list | None:
        """从 Redis Stream 读取消息"""
        try:
            return await self.redis.xread(
                streams={stream_name: last_id},
                count=self.read_count,
                block=self.read_block_ms,
            )
        except TimeoutError:
            return None
        except Exception as e:
            logger.error(f"Error reading from stream {stream_name}: {e}", exc_info=True)
            raise

    async def get_task_events(
            self, task_id: str, last_id: str = "0-0", timeout_seconds: float | None = None
    ) -> AsyncGenerator[Any, None]:
        """
        消费任务事件流

        从 Redis Stream 读取任务执行事件，并以异步生成器形式返回。
        支持三种事件类型:
            - chunk: 中间结果事件（返回 text_message）
            - success: 任务成功完成（生成器正常结束）
            - failed: 任务失败（抛出异常）

        Args:
            task_id: 任务 ID
            last_id: 开始读取的消息 ID，默认从头开始
            timeout_seconds: 超时时间（秒），None 表示无限等待

        Yields:
            任务事件数据（通常为字典）

        Raises:
            Exception: 任务失败时抛出包含错误信息的异常
            TimeoutError: 超时时抛出
        """
        stream_name = build_stream_name_key(task_id, self.task_name)
        start_time = asyncio.get_event_loop().time()

        logger.info(
            f"Starting to consume events for task {task_id} from stream {stream_name}"
        )

        try:
            while True:
                self._check_timeout(start_time, timeout_seconds, task_id)

                messages = await self._read_messages(stream_name, last_id)
                if not messages:
                    await asyncio.sleep(0.001)  # Reduced from 10ms to 1ms
                    continue

                for _stream_key, msgs in messages:
                    for message_id, data in msgs:
                        last_id = decode_bytes(message_id)
                        event_type = decode_data_field(data, "event")
                        event_data = parse_json_field(data, "data", default={})
                        event_result = _handle_event(event_type, event_data, task_id)

                        match event_result.action:
                            case EventAction.YIELD:
                                yield event_result.data
                            case EventAction.RETURN:
                                return
                            case EventAction.RAISE:
                                raise event_result.data

        except asyncio.CancelledError:
            logger.info(f"Task {task_id} consumption cancelled")
            raise
        except Exception as e:
            logger.error(f"Error consuming task {task_id}: {e}", exc_info=True)
            raise
