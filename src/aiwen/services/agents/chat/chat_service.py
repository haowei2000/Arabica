#!/usr/bin/env python3
"""
Chat Service - 聊天服务

功能描述:
    提供聊天服务的业务逻辑，包括队列模式和直接模式的聊天处理。
    负责协调对话、消息、任务的创建，以及事件流的生成。

作者: Claude
创建日期: 2025/12/30
版本: v1.0.0

公司名称: 艾普工华(武汉)有限责任公司
版权信息: © 2025 艾普工华(武汉)有限责任公司. 保留所有权利.

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
    prepare_conversation,
    stream_and_finalize,
)
from aiwen.services.agents.crud.agent_template_crud import AgentTemplateCRUD
from aiwen.services.agents.crud.app_crud import AppCRUD
from aiwen.services.agents.crud.conversation_crud import ConversationCRUD
from aiwen.services.agents.crud.message_crud import MessageCRUD
from aiwen.services.agents.crud.task_crud import TaskCRUD
from aiwen.services.agents.runtime import AgentRuntime
from aiwen.utils.json_utils import dumps as json_dumps
from aiwen.utils.sse import sse

logger = logging.getLogger(__name__)


class ChatService:
    """
    聊天服务

    提供队列模式和直接模式的聊天服务业务逻辑。

    使用示例:
        ```python
        from aiwen.services.agents.chat_service import ChatService

        chat_service = ChatService()

        # 队列模式聊天
        async for event in chat_service.chat_queue_based(
            app_id=app_id,
            payload=payload,
            task_crud=task_crud,
            conversation_crud=conversation_crud,
            message_crud=message_crud,
            redis_client=redis_client
        ):
            yield event

        # 直接模式聊天
        async for event in chat_service.chat_direct(
            app_id=app_id,
            payload=payload,
            app_crud=app_crud,
            template_crud=template_crud,
            conversation_crud=conversation_crud,
            message_crud=message_crud,
            runtime=runtime
        ):
            yield event
        ```
    """

    async def chat_queue_based(
        self,
        *,
        app_id: UUID,
        payload: TextMessage,
        task_crud: TaskCRUD,
        conversation_crud: ConversationCRUD,
        message_crud: MessageCRUD,
        redis_client: Any,
    ) -> AsyncGenerator[str, None]:
        """
        队列模式聊天

        通过 Redis 队列异步处理聊天请求，适用于生产环境。
        任务由 Worker 异步处理，结果通过 Redis Pub/Sub 实时推送。

        Args:
            app_id: 应用 ID
            payload: 聊天消息载荷
            task_crud: 任务 CRUD 服务
            conversation_crud: 对话 CRUD 服务
            message_crud: 消息 CRUD 服务
            redis_client: Redis 客户端

        Yields:
            SSE 格式的事件字符串

        Raises:
            Exception: 聊天处理失败时抛出异常
        """
        # 1. Prepare conversation
        conversation = await prepare_conversation(
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

        # 3. Create task
        task = await task_crud.create_task(
            app_id=app_id,
            task_type="chat",
            payload=payload,
        )
        logger.info(f"Created task {task.id} for message {message.id}")

        # 4. Prepare payload with app, conversation and message IDs
        payload_dict = self._prepare_payload_dict(payload, app_id, conversation, message)

        # 5. Publish task to Redis
        await self._publish_task_to_redis(
            redis_client=redis_client,
            task_id=task.id,
            task_payload=payload_dict,
        )

        # 6. Stream events from Redis
        async for event in self._stream_queue_events(
            redis_client=redis_client,
            task_id=task.id,
            conversation=conversation,
            message=message,
            message_crud=message_crud,
            conversation_crud=conversation_crud,
        ):
            yield event

    async def chat_direct(
        self,
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
        conversation = await prepare_conversation(
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
        async for event in self._stream_direct_events(
            app_id=app_id,
            payload=payload,
            conversation=conversation,
            message=message,
            app_crud=app_crud,
            template_crud=template_crud,
            message_crud=message_crud,
            conversation_crud=conversation_crud,
            runtime=runtime,
        ):
            yield event

    async def cancel_task(
        self,
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

                    # Publish cancellation event
                    await redis_client.publish(
                        f"agent:task:{task_id}",
                        json_dumps({"event": "cancelled", "data": "Task was cancelled by user"})
                    )
                    return True
            except KeyError:
                # Task not in runtime, might be in queue mode
                pass

            # 2. Try to cancel in queue mode by publishing cancel event
            # The worker will handle the cancellation
            await redis_client.publish(
                f"agent:task:{task_id}:cancel",
                json_dumps({"action": "cancel", "task_id": str(task_id)})
            )

            # Also publish to the main task channel
            await redis_client.publish(
                f"agent:task:{task_id}",
                json_dumps({"event": "cancelled", "data": "Task was cancelled by user"})
            )

            logger.info(f"Sent cancellation signal for task {task_id} (queue mode)")
            return True

        except Exception as e:
            logger.error(f"Error cancelling task {task_id}: {e}", exc_info=True)
            return False

    # ----------------- Private Helper Methods -----------------

    def _prepare_payload_dict(
        self,
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

    async def _publish_task_to_redis(
        self,
        *,
        redis_client: Any,
        task_id: UUID,
        task_payload: dict,
    ) -> None:
        """
        发布任务到 Redis

        Args:
            redis_client: Redis 客户端
            task_id: 任务 ID
            task_payload: 任务载荷（包含 app_id）

        Raises:
            Exception: 发布失败时抛出异常
        """
        try:
            await redis_client.publish(
                "agent_tasks",
                json_dumps(
                    {
                        "task_id": str(task_id),
                        "payload": task_payload,
                    }
                ),
            )
            logger.info(f"Published task {task_id} to Redis channel 'agent_tasks'")
        except Exception as e:
            logger.error(f"Failed to publish task {task_id} to Redis: {e}", exc_info=True)
            raise

    async def _stream_queue_events(
        self,
        *,
        redis_client: Any,
        task_id: UUID,
        conversation: Conversation,
        message: Message,
        message_crud: MessageCRUD,
        conversation_crud: ConversationCRUD,
    ) -> AsyncGenerator[str, None]:
        """
        从 Redis 队列流式读取事件

        Args:
            redis_client: Redis 客户端
            task_id: 任务 ID
            conversation: 对话对象
            message: 消息对象
            message_crud: 消息 CRUD 服务
            conversation_crud: 对话 CRUD 服务

        Yields:
            SSE 格式的事件字符串

        Raises:
            Exception: 事件处理失败时抛出异常
        """
        channel = f"agent:task:{task_id}"
        pubsub = redis_client.pubsub()

        try:
            await pubsub.subscribe(channel)
            logger.info(f"Subscribed to Redis channel '{channel}'")

            async def redis_stream():
                """内部 Redis 消息流生成器"""
                async for msg in pubsub.listen():
                    if msg["type"] != "message":
                        continue

                    data = msg["data"]
                    # Handle both bytes and str from Redis
                    if isinstance(data, bytes):
                        data = data.decode()

                    # Parse the JSON event data from worker
                    try:
                        event_data = json.loads(data)
                        event_type = event_data.get("event")
                        event_payload = event_data.get("data")

                        # Only yield chunk events, skip internal events
                        if event_type == "chunk":
                            yield event_payload
                        elif event_type == "success":
                            # Task completed successfully
                            logger.info(f"Task {task_id} completed successfully")
                            break
                        elif event_type == "failed":
                            # Task failed
                            logger.error(f"Task {task_id} failed: {event_payload}")
                            raise Exception(event_payload)
                    except json.JSONDecodeError:
                        # If it's not JSON, yield as-is (for backward compatibility)
                        logger.warning(f"Received non-JSON data from channel '{channel}': {data}")
                        yield data

            # Stream and finalize
            async for event in stream_and_finalize(
                stream_iter=redis_stream(),
                conversation=conversation,
                message=message,
                message_crud=message_crud,
                conversation_crud=conversation_crud,
            ):
                yield event

        except Exception as e:
            logger.error(f"Error streaming events for task {task_id}: {e}", exc_info=True)
            await message_crud.update(
                str(message.id),
                MessageUpdate(status="failed", error=str(e)),
            )
            yield sse("error", str(e))
        finally:
            await pubsub.unsubscribe(channel)
            logger.info(f"Unsubscribed from Redis channel '{channel}'")

    async def _stream_direct_events(
        self,
        *,
        app_id: UUID,
        payload: TextMessage,
        conversation: Conversation,
        message: Message,
        app_crud: AppCRUD,
        template_crud: AgentTemplateCRUD,
        message_crud: MessageCRUD,
        conversation_crud: ConversationCRUD,
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
            conversation_crud: 对话 CRUD 服务
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
                message_crud=message_crud,
                conversation_crud=conversation_crud,
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
