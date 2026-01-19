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
from collections.abc import AsyncGenerator
import json
import logging
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from aiwen.models.agents.conversation import Conversation
from aiwen.models.agents.message import Message
from aiwen.schemas.agents.input import TextMessage
from aiwen.schemas.agents.message import MessageUpdate
from aiwen.services.agents.agent_registry import AgentRegistry
from aiwen.services.agents.app_factory import AppAgentFactory
from aiwen.services.agents.chat.chat_helper import (
    create_message,
    create_conversation,
    stream_and_finalize,
)
from aiwen.services.agents.crud.agent_template_crud import AgentTemplateCRUD
from aiwen.services.agents.crud.app_crud import AppCRUD
from aiwen.services.agents.crud.conversation_crud import ConversationCRUD
from aiwen.services.agents.crud.message_crud import MessageCRUD
from aiwen.services.agents.crud.task_crud import AgentTaskCRUD
from aiwen.services.agents.runtime import AgentRuntime
from aiwen.utils.json_utils import dumps as json_dumps
from aiwen.utils.sse import sse
from aiwen.workers.task_consumer import TaskConsumer
from aiwen.workers.task_producer import TaskProducer
from redis.asyncio import Redis as AsyncRedis

logger = logging.getLogger(__name__)


class StartTaskResult(BaseModel):
    """启动任务的返回结果"""
    task_id: str
    conversation_id: str
    message_id: str

async def _stream_queue_events(
        *,
    task_consumer: TaskConsumer,
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
        logger.info(f"Starting to consume events for task {task_id} via TaskConsumer")

        async def consumer_stream():
            """内部 TaskConsumer 消息流生成器"""
            async for event_payload in task_consumer.consume_task_events(
                task_id=str(task_id),
                task_type="chat",
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


async def _stream_direct_events(
        *,
    app_id: UUID,
    payload: TextMessage,
    conversation: Conversation,
    message: Message,
    app_crud: AppCRUD,
    template_crud: AgentTemplateCRUD,
    message_crud: MessageCRUD,
        runtime: AgentRuntime,
) -> AsyncGenerator[str, None]:
    """
    直接模式事件流生成器

    Args:
        app_id: 应用 ID
        payload: 聊天消息载荷
        conversation: 对话对象
        message: 消息对象
        app_crud: 应用 CRUD 服务
        template_crud: Agent 模板 CRUD 服务
        message_crud: 消息 CRUD 服务
        runtime: Agent 运行时

    Yields:
        SSE 格式的事件字符串

    Raises:
        Exception: Agent 执行失败时抛出异常
    """
    task_id = None

    try:
        # 1. Get app from database
        app = await app_crud.get_app_by_id(app_id)
        if not app:
            raise ValueError(f"App with ID '{app_id}' not found")
        logger.info(f"Retrieved app {app_id} from database")

        # 2. Get agent template
        if not app.agent_template_id:
            raise ValueError(f"App '{app_id}' has no associated agent template")

        template = await template_crud.get_template_by_id(app.agent_template_id)
        if not template:
            raise ValueError(f"Agent template '{app.agent_template_id}' not found")
        logger.info(f"Retrieved agent template '{template.template_code}'")

        # 3. Check if template is registered
        if not AgentRegistry.is_registered(template.template_code):
            raise ValueError(
                f"Agent type '{template.template_code}' is not registered in AgentRegistry"
            )

        # 4. Create agent instance using factory
        factory = AppAgentFactory(
            appid=str(app.id),
            template_code=template.template_code,
            app_config=app.config or {},
        )
        payload_dict = payload.model_dump(mode="json")
        agent_instance = factory.create(payload_dict)
        logger.info(f"Created agent instance for template '{template.template_code}'")

        # 5. Attach to runtime (using message.id as task_id)
        task_id = message.id
        runtime.attach(task_id, agent_instance)
        logger.info(f"Attached agent instance to runtime with task_id {task_id}")

        # 6. Stream execution
        stream = agent_instance.stream(payload_dict)

        async for event in stream_and_finalize(
            stream_iter=stream,
            conversation=conversation,
            message=message,
        ):
            yield event

    except Exception as e:
        logger.error(f"Error in direct chat for app {app_id}: {e}", exc_info=True)
        await message_crud.update(
            str(message.id),
            MessageUpdate(status="failed", error=str(e)),
        )
        yield sse("error", str(e))
    finally:
        # 7. Release agent instance
        if task_id:
            runtime.release(task_id)
            logger.info(f"Released agent instance from runtime (task_id: {task_id})")


def _prepare_task_payload(
        payload: TextMessage,
    app_id: UUID,
    conversation: Conversation,
    message: Message,
) -> dict:
    """
    准备载荷字典

    Args:
        payload: 原始载荷
        app_id: 应用 ID
        conversation: 对话对象
        message: 消息对象

    Returns:
        包含应用、对话和消息 ID 的载荷字典
    """
    payload_dict = (
        payload.model_dump(mode="json")
        if isinstance(payload, BaseModel)
        else payload
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
        fields={"event": event, "payload": json_dumps(data)},
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

    支持队列模式（Celery）和直接模式的任务取消。

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

        # 2. Cancel in queue mode using Celery revoke
        from aiwen.celery_app import celery_app

        celery_app.control.revoke(str(task_id), terminate=True)
        logger.info(f"Revoked Celery task {task_id}")

        # Publish cancelled event to Stream for consumer
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
    task_consumer: TaskConsumer,
) -> AsyncGenerator[str, None]:
    """
    订阅任务消息流

    通过 TaskConsumer 从 Redis Stream 读取任务的消息流，返回 SSE 格式的事件。

    Args:
        task_id: 任务 ID
        task_consumer: 任务消费者

    Yields:
        SSE 格式的事件字符串
    """
    try:
        logger.info(f"Starting to consume messages for task {task_id} via TaskConsumer")

        # Yield metadata event with task_id
        yield sse("metadata", {"task_id": str(task_id)})

        async for event_payload in task_consumer.consume_task_events(
            task_id=str(task_id),
            task_type="chat",
        ):
            yield sse("chunk", event_payload)

        # Task completed successfully (consume_task_events returns normally on success)
        logger.info(f"Task {task_id} completed successfully")
        yield sse("success", {"message": "Task completed"})

    except Exception as e:
        error_msg = str(e)
        if "cancelled" in error_msg.lower():
            logger.info(f"Task {task_id} was cancelled")
            yield sse("cancelled", {"message": "Task was cancelled"})
        else:
            logger.error(f"Error streaming events for task {task_id}: {e}", exc_info=True)
            yield sse("error", error_msg)


async def start_task(
        *,
    app_id: UUID,
    user_id: UUID,
    payload: TextMessage,
    task_crud: AgentTaskCRUD,
    conversation_crud: ConversationCRUD,
    message_crud: MessageCRUD,
    task_producer: TaskProducer,
) -> StartTaskResult:
    """
    启动聊天任务（不等待结果）

    创建对话、消息和任务，发布到 Redis 队列后立即返回任务 ID。
    客户端可以使用任务 ID 订阅消息流或取消任务。

    Args:
        app_id: 应用 ID
        user_id: 用户 ID
        payload: 聊天消息载荷
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
        payload=payload,
        conversation_crud=conversation_crud,
    )
    logger.info(f"Prepared conversation {conversation.id} for app {app_id}")

    # 2. Create message
    message = await create_message(
        app_id=app_id,
        conversation_id=conversation.id,
        text_message=payload,
        message_crud=message_crud,
    )
    logger.info(f"Created message {message.id} in conversation {conversation.id}")

    # 3. Create a task
    task = await task_crud.create_agent_task(
        app_id=app_id,
        user_id=user_id,
        task_type="chat",
        payload=payload,
    )
    logger.info(f"Created task {task.id} for message {message.id}")

    # 4. Prepare a payload with app, conversation and message IDs
    payload_dict = _prepare_task_payload(payload, app_id, conversation, message)

    # 5. Publish a task using TaskProducer
    await task_producer.publish_task(
        task_id=UUID(str(task.id)),
        payload=payload_dict,
    )

    return StartTaskResult(
        task_id=str(task.id),
        conversation_id=str(conversation.id),
        message_id=str(message.id),
    )


async def chat_direct(
        *,
    app_id: UUID,
    payload: TextMessage,
    app_crud: AppCRUD,
    template_crud: AgentTemplateCRUD,
    conversation_crud: ConversationCRUD,
    message_crud: MessageCRUD,
    runtime: AgentRuntime,
) -> AsyncGenerator[str, None]:
    """
    直接模式聊天

    直接执行聊天请求，不通过队列。适用于测试或低延迟场景。

    Args:
        app_id: 应用 ID
        payload: 聊天消息载荷
        app_crud: 应用 CRUD 服务
        template_crud: Agent 模板 CRUD 服务
        conversation_crud: 对话 CRUD 服务
        message_crud: 消息 CRUD 服务
        runtime: Agent 运行时

    Yields:
        SSE 格式的事件字符串

    Raises:
        Exception: 聊天处理失败时抛出异常
    """
    # 1. Prepare conversation
    conversation = await create_conversation(
        app_id=app_id,
        payload=payload,
        conversation_crud=conversation_crud,
    )
    logger.info(f"Prepared conversation {conversation.id} for app {app_id}")

    # 2. Create message
    message = await create_message(
        app_id=app_id,
        conversation_id=conversation.id,
        text_message=payload,
        message_crud=message_crud,
    )
    logger.info(f"Created message {message.id} in conversation {conversation.id}")

    # 3. Stream events from direct agent execution
    async for event in _stream_direct_events(
        app_id=app_id,
        payload=payload,
        conversation=conversation,
        message=message,
        app_crud=app_crud,
        template_crud=template_crud,
        message_crud=message_crud,
            runtime=runtime,
    ):
        yield event


async def chat_queue_based(
        *,
    app_id: UUID,
    user_id: UUID,
    payload: TextMessage,
    task_crud: AgentTaskCRUD,
    conversation_crud: ConversationCRUD,
    message_crud: MessageCRUD,
    task_producer: TaskProducer,
    task_consumer: TaskConsumer,
) -> AsyncGenerator[str, None]:
    """
    队列模式聊天

    通过 Redis 队列异步处理聊天请求，适用于生产环境。
    任务由 Worker 异步处理，结果通过 Redis Stream 实时推送。

    Args:
        app_id: 应用 ID
        user_id: 用户 ID
        payload: 聊天消息载荷
        task_crud: 任务 CRUD 服务
        conversation_crud: 对话 CRUD 服务
        message_crud: 消息 CRUD 服务
        task_producer: 任务生产者
        task_consumer: 任务消费者 (用于读取事件)

    Yields:
        SSE 格式的事件字符串

    Raises:
        Exception: 聊天处理失败时抛出异常
    """
    # 1. Prepare conversation
    conversation = await create_conversation(
        app_id=app_id,
        payload=payload,
        conversation_crud=conversation_crud,
    )

    logger.info(f"Prepared conversation {conversation.id} for app {app_id}")

    # 2. Create a message
    message = await create_message(
        app_id=app_id,
        conversation_id=conversation.id,
        text_message=payload,
        message_crud=message_crud,
    )
    logger.info(f"Created message {message.id} in conversation {conversation.id}")

    # 3. Create task
    task = await task_crud.create_agent_task(
        app_id=app_id,
        user_id=user_id,
        task_type="chat",
        payload=payload,
    )
    logger.info(f"Created task {task.id} for message {message.id}")

    # 4. Prepare a payload with app, conversation and message IDs
    payload_dict = _prepare_task_payload(payload, app_id, conversation, message)

    # 5. Publish task using TaskProducer
    await task_producer.publish_task(
        task_id=task.id,
        payload=payload_dict,
    )

    # 6. Stream events from TaskConsumer
    async for event in _stream_queue_events(
        task_consumer=task_consumer,
        task_id=task.id,
        conversation=conversation,
        message=message,
        message_crud=message_crud,
    ):
        yield event