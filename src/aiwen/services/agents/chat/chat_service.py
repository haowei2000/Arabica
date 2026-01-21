#!/usr/bin/env python3
"""
Chat Service - 聊天服务

功能描述:
    提供聊天服务的业务逻辑，包括队列模式和直接模式的聊天处理。
    负责协调对话、消息、任务的创建，以及事件流的生成。


依赖模块:
    - chat_helper: 辅助函数
    - CRUD services: 数据库操作
    - Agent Registry: Agent 注册管理
"""
import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from aiwen.models.agents.conversation import Conversation
from aiwen.models.agents.message import Message
from aiwen.schemas.agents.input import TextInput
from aiwen.schemas.agents.message import MessageUpdate
from aiwen.schemas.chat import StartTaskResult
from aiwen.services.agents.chat.chat_helper import (
    create_message,
    create_conversation,
    stream_and_finalize,
)
from aiwen.services.agents.crud.conversation_crud import ConversationCRUD
from aiwen.services.agents.crud.message_crud import MessageCRUD
from aiwen.services.agents.crud.task_crud import AgentTaskCRUD
from aiwen.services.agents.runtime import AgentRuntime
from aiwen.utils.json_utils import dumps as json_dumps
from aiwen.utils.sse import sse
from aiwen.workers.task_consumer import AgentTaskConsumer
from aiwen.workers.task_producer import AgentTaskProducer

logger = logging.getLogger(__name__)





async def _stream_queue_events(
        *,
        task_consumer: AgentTaskConsumer,
        task_id: UUID,
        conversation: Conversation,
        message: Message,
        message_crud: MessageCRUD,
) -> AsyncGenerator[str, None]:
    """
    从 Redis Stream 流式读取事件

    Args:
        task_consumer: 任务消费者
        task_id: 任务 ID
        conversation: 对话对象
        message: 消息对象
        message_crud: 消息 CRUD 服务

    Yields:
        SSE 格式的事件字符串

    Raises:
        Exception: 事件处理失败时抛出异常
    """
    try:
        logger.info(f"Starting to consume events for task {task_id} via AgentTaskConsumer")

        async def consumer_stream():
            """内部 AgentTaskConsumer 消息流生成器"""
            async for event_payload in task_consumer.consume_task_events(
                    task_id=str(task_id),
            ):
                yield event_payload

        # Stream and finalize
        async for event in stream_and_finalize(
                stream_iter=consumer_stream(),
                conversation=conversation,
                message=message,
        ):
            yield event

    except Exception as e:
        logger.error(f"Error streaming events for task {task_id}: {e}", exc_info=True)
        await message_crud.update(
            str(message.id),
            MessageUpdate(status="failed", error=str(e)),
        )
        yield sse("error", str(e))


def _prepare_task_payload(
        text_message: TextInput,
        app_id: UUID,
        conversation: Conversation,
        message: Message,
) -> dict:
    """
    准备载荷字典

    Args:
        text_message: 原始载荷
        app_id: 应用 ID
        conversation: 对话对象
        message: 消息对象

    Returns:
        包含应用、对话和消息 ID 的载荷字典
    """
    payload_dict = (
        text_message.model_dump(mode="json")
        if isinstance(text_message, BaseModel)
        else text_message
    )
    payload_dict.update(
        {
            "app_id": str(app_id),
            "conversation_id": str(conversation.id),
            "message_id": str(message.id),
        }
    )
    return payload_dict


async def _publish_event_to_stream(
        redis_client: Any,
        task_id: UUID,
        event: str,
        data: str,
) -> None:
    """
    发布事件到 Redis Stream

    Args:
        redis_client: Redis 客户端
        task_id: 任务 ID
        event: 事件类型
        data: 事件数据
    """
    stream_name = f"agent:task:chat:{task_id}:events"
    await redis_client.xadd(
        name=stream_name,
        fields={"event": event, "text_message": json_dumps(data)},
        maxlen=1000,
        approximate=True
    )


async def cancel_task(
        *,
        task_id: UUID,
        redis_client: Any,
        runtime: AgentRuntime,
) -> bool:
    """
    取消正在运行的任务

    支持队列模式和直接模式的任务取消。

    Args:
        task_id: 任务 ID
        redis_client: Redis 客户端
        runtime: Agent 运行时

    Returns:
        True if task was found and cancelled, False otherwise
    """
    try:
        # 1. Check if task is running in direct mode (runtime)
        try:
            agent = runtime.get(task_id)
            if agent:
                # Release the agent from runtime
                runtime.release(task_id)
                logger.info(f"Cancelled task {task_id} from runtime (direct mode)")

                # Publish cancellation event to Stream
                await _publish_event_to_stream(
                    redis_client, task_id, "cancelled", "Task was cancelled by user"
                )
                return True
        except KeyError:
            # Task not in runtime, might be in queue mode
            pass

        # 2. Try to cancel in queue mode by publishing cancel signal to worker via Pub/Sub
        await redis_client.publish(
            f"agent:task:{task_id}:cancel",
            json_dumps({"action": "cancel", "task_id": str(task_id)})
        )

        # Also publish cancelled event to Stream for consumer
        await _publish_event_to_stream(
            redis_client, task_id, "cancelled", "Task was cancelled by user"
        )

        logger.info(f"Sent cancellation signal for task {task_id} (queue mode)")
        return True

    except Exception as e:
        logger.error(f"Error cancelling task {task_id}: {e}", exc_info=True)
        return False


async def get_task_messages(
        *,
        task_id: UUID,
        task_consumer: AgentTaskConsumer,
) -> AsyncGenerator[str, None]:
    """
    订阅任务消息流

    通过 AgentTaskConsumer 从 Redis Stream 读取任务的消息流，返回 SSE 格式的事件。

    Args:
        task_id: 任务 ID
        task_consumer: 任务消费者

    Yields:
        SSE 格式的事件字符串
    """
    try:
        logger.info(f"Starting to consume messages for task {task_id} via AgentTaskConsumer")

        # Yield metadata event with task_id
        yield sse("metadata", {"task_id": str(task_id)})

        async for event_payload in task_consumer.consume_task_events(
                task_id=str(task_id),
        ):
            yield sse("chunk", event_payload)

        # Task completed successfully (consume_task_events returns normally on success)
        logger.info(f"Task {task_id} completed successfully")
        yield sse("success", {"input": "Task completed"})

    except Exception as e:
        error_msg = str(e)
        if "cancelled" in error_msg.lower():
            logger.info(f"Task {task_id} was cancelled")
            yield sse("cancelled", {"input": "Task was cancelled"})
        else:
            logger.error(f"Error streaming events for task {task_id}: {e}", exc_info=True)
            yield sse("error", error_msg)


async def start_task(
        *,
        app_id: UUID,
        user_id: UUID,
        text_message: TextInput,
        task_crud: AgentTaskCRUD,
        conversation_crud: ConversationCRUD,
        message_crud: MessageCRUD,
        task_producer: AgentTaskProducer,
) -> StartTaskResult:
    """
    启动聊天任务（不等待结果）

    创建对话、消息和任务，发布到 Redis 队列后立即返回任务 ID。
    客户端可以使用任务 ID 订阅消息流或取消任务。

    Args:
        app_id: 应用 ID
        user_id: 用户 ID
        text_message: 聊天消息载荷
        task_crud: 任务 CRUD 服务
        conversation_crud: 对话 CRUD 服务
        message_crud: 消息 CRUD 服务
        task_producer: 任务生产者

    Returns:
        StartTaskResult: 包含 task_id, conversation_id, message_id
    """
    # 1. Prepare conversation
    conversation = await create_conversation(
        app_id=app_id,
        text_message=text_message,
        conversation_crud=conversation_crud,
    )
    logger.info(f"Prepared conversation {conversation.id} for app {app_id}")

    # 2. Create input
    message = await create_message(
        app_id=app_id,
        conversation_id=conversation.id,
        text_message=text_message,
        message_crud=message_crud,
    )
    logger.info(f"Created input {message.id} in conversation {conversation.id}")

    # 3. Create a task
    task = await task_crud.create_agent_task(
        app_id=app_id,
        user_id=user_id,
        task_type="agent:streams",
        payload=text_message,
    )
    logger.info(f"Created task {task.id} for input {message.id}")

    # 4. Prepare a text_message with app, conversation and input IDs
    task_payload = _prepare_task_payload(text_message, app_id, conversation, message)

    # 5. Publish a task using AgentTaskProducer
    await task_producer.publish_task(
        task_id=UUID(str(task.id)),
        payload=task_payload,
    )

    return StartTaskResult(
        task_id=str(task.id),
        conversation_id=str(conversation.id),
        message_id=str(message.id),
    )
