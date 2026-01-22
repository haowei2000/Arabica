#!/usr/bin/env python3
"""
Agent Worker - 异步处理 Agent 任务

功能描述:
    监听 Redis 任务队列，异步执行 Agent 任务，支持流式输出和运行时管理。

作者: haowei
创建日期: 2025/12/23
最后修改: 2025/12/30 17:00
修改人员: Claude
版本: v1.2.1

更新日志:
    v1.2.1 (2025/12/30):
        - 修复 agent_instance.text_message 属性不存在的错误
        - 将 text_message 作为参数传递给 stream 方法

    v1.2.0 (2025/12/30):
        - 适配新的 Redis 消息格式（app_id 在 text_message 内部）
        - 增强消息解析的错误处理和验证



依赖模块:
    - redis.asyncio: Redis 异步客户端
    - aiwen.services.agents: Agent 管理和运行时
"""

# aiwen/workers/task_worker.py
from cryptography.hazmat.asn1.asn1 import U
import asyncio
import json
import logging
from typing import Any
from uuid import UUID

import redis.asyncio as redis_async
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.extensions.database import get_session
from aiwen.schemas.agents.input import TextInput
from aiwen.schemas.agents.message import MessageCreate
from aiwen.schemas.task.payload import TaskPayload
from aiwen.services.agents.agent_registry import AgentRegistry
from aiwen.services.agents.app_factory import AppAgentFactory
from aiwen.services.agents.crud.agent_template_crud import AgentTemplateCRUD
from aiwen.services.agents.crud.app_crud import AppCRUD
from aiwen.services.agents.crud.message_crud import MessageCRUD
from aiwen.services.agents.crud.task_crud import AgentTaskCRUD
from aiwen.services.agents.runtime import AgentRuntime
from aiwen.utils.json_utils import dumps as json_dumps
from aiwen.workers.task_utils import (
    decode_data_field,
    parse_json_field,
    build_stream_name_key,
)

logger = logging.getLogger(__name__)


async def _validate_template_registration(template):
    """验证模板是否已注册"""
    logger.info(f"Checking if template '{template.template_code}' is registered...")
    logger.info(f"Available templates in registry: {AgentRegistry.list()}")

    if not AgentRegistry.is_registered(template.template_code):
        available_templates = AgentRegistry.list()
        error_msg = (
            f"Agent template '{template.template_code}' is not registered in AgentRegistry. "
            f"Available templates: {available_templates if available_templates else 'NONE - Registry is empty!'}"
        )
        logger.error(error_msg)
        raise ValueError(error_msg)


async def _get_agent_template(template_crud, app):
    """获取agent模板"""
    if not app.agent_template_id:
        raise ValueError("App has no associated agent template")

    template = await template_crud.get_template_by_id(app.agent_template_id)
    if not template:
        raise ValueError(f"Agent template '{app.agent_template_id}' not found")
    return template


async def _get_app_by_id(app_crud, app_id):
    """根据ID获取app配置"""
    app = await app_crud.get_app_by_id(
        UUID(app_id) if isinstance(app_id, str) else app_id
    )
    if not app:
        raise ValueError(f"App with ID '{app_id}' not found")
    return app


class AgentWorker:
    """
    Worker for processing agent tasks asynchronously.

    使用 AgentRuntime 管理 agent 实例生命周期，支持流式输出和任务状态跟踪。
    """

    def __init__(self, redis_client: redis_async.Redis):
        """
        初始化 Agent Worker.

        Args:
            redis_client: Redis 异步客户端
            db: 数据库会话工厂
        """
        self.redis_client = redis_client
        self.runtime = AgentRuntime()  # Agent 运行时管理器
        self.task_name = "agent"

    async def start(self):
        logger.info("Starting AgentWorker...")
        try:
            # Consume tasks from Redis Stream
            await self._consume_task_stream()
        except asyncio.CancelledError:
            logger.info("AgentWorker cancelled")
        except Exception as e:
            logger.error(f"AgentWorker error: {e}", exc_info=True)

    async def _consume_task_stream(self):
        """Consume tasks from Redis Stream using XREAD"""

        logger.info(f"Starting to consume tasks from stream '{self.task_name}'")

        while True:
            try:
                # 读取Redis流中的任务
                messages = await self.redis_client.xread(
                    streams={self.task_name: "$"}, count=1, block=1000
                )

                if messages:
                    # messages结构为 [(stream_name, [(message_id, data), ...])]
                    stream_name, msg_list = messages[0]
                    for message_id, data in msg_list:
                        try:
                            task_id = decode_data_field(data, "task_id")
                            payload = parse_json_field(data, "input", default={})
                            payload = TaskPayload.model_validate(payload)
                            async with get_session("aiwen") as db:
                                await self.process_task(UUID(task_id), payload, db)
                        except Exception as e:
                            logger.error(f"Error processing task: {e}", exc_info=True)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error reading from stream: {e}", exc_info=True)
                await asyncio.sleep(1)  # 等待后重试

    async def process_task(
        self, task_id: UUID, task_payload: TaskPayload, db: AsyncSession
    ):
        """
        处理单个 agent 任务.

        Args:
            task_payload: Redis Stream 消息数据 (dict with bytes keys from XREAD)
        """

        try:
            # 解析消息数据 (from Redis Stream)
            app_id = task_payload.app_id
            if not app_id:
                raise ValueError("App ID is required")
            input = task_payload.input

            logger.info(f"Processing task {task_id} for app {app_id}")

            # 执行任务的主要逻辑
            await self._execute_task_logic(task_id, UUID(app_id), input, db)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in task message: {e}")
        except Exception as e:
            logger.error(f"Error in process_task: {e}", exc_info=True)
            # 如果有 task_id，确保更新任务状态和释放资源
            if task_id:
                await self._handle_task_error(task_id, str(e))

    def _parse_stream_message(self, message_data: dict) -> dict:
        """
        解析 Redis Stream 消息数据

        Stream 消息格式 (from XREAD):
        {
            b"task_id": b"...",
            b"text_message": b'{"app_id": "...", ...}'
        }

        Args:
            message_data: Redis Stream 消息字典 (bytes keys/values)

        Returns:
            解析后的任务数据

        Raises:
            ValueError: 如果消息格式无效或缺少必需字段
        """
        # Decode bytes keys and values
        decoded_data = {}
        for key, value in message_data.items():
            k = key.decode() if isinstance(key, bytes) else key
            v = value.decode() if isinstance(value, bytes) else value
            decoded_data[k] = v

        logger.info(f"Decoded stream message: {decoded_data}")

        # Validate required fields
        if "task_id" not in decoded_data:
            raise ValueError("Missing required field: task_id")

        # Parse text_message JSON
        text_messages_str = decoded_data.get("text_message", "{}")
        try:
            payload = json.loads(text_messages_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in text_message: {e}")

        logger.info(f"Extracted text_message: {payload}")

        app_id = payload.get("app_id")
        if not app_id:
            raise ValueError(
                "Missing required field: app_id in text_message. "
                "Ensure ChatService is using the new text_message format (v1.2.0+)"
            )

        parsed_result = {
            "task_id": UUID(decoded_data["task_id"]),
            "app_id": app_id,
            "text_message": payload,
        }
        logger.info(f"Parsed result: {parsed_result}")

        return parsed_result

    async def _execute_task_logic(
        self, task_id: UUID, app_id: UUID, input: TextInput, db: AsyncSession
    ):
        """执行任务的主要逻辑"""
        try:
            # 获取数据库会话
            app_crud = AppCRUD(db)
            template_crud = AgentTemplateCRUD(db)
            task_crud = AgentTaskCRUD(db)
            try:
                # 1. 从数据库获取 app 配置
                app = await _get_app_by_id(app_crud, app_id)

                # 2. 获取 agent 模板
                template = await _get_agent_template(template_crud, app)

                # 3. 检查模板代码是否在 Registry 中注册
                await _validate_template_registration(template)

                # 4. 创建 agent 实例并附加到运行时
                agent_instance = await self._create_and_attach_agent(
                    app, template, input, task_id
                )

                logger.info(
                    f"Agent instance created for task {task_id}, type: {template.template_code}"
                )

                # 5. 流式执行 agent 并发送事件
                result_data = await self._execute(
                    agent_instance, input.model_dump(), task_id
                )

                # 6. 更新任务状态为成功
                await task_crud.update_agent_task_status(
                    task_id, "success", result=result_data
                )
                await self.publish_event(
                    task_id, {"event": "success", "data": json_dumps(result_data)}
                )

                logger.info(
                    f"Task {task_id} completed successfully with {len(result_data.get('chunks', []))} chunks"
                )

            finally:
                # 8. 释放 agent 实例
                self.runtime.release(task_id)
                logger.debug(f"Agent instance released for task {task_id}")

        except ValueError as e:
            await self._handle_task_error(task_id, str(e))
        except Exception as e:
            logger.error(f"Task {task_id} failed (Exception): {e}", exc_info=True)
            await self._handle_task_error(task_id, str(e))

    async def _create_and_attach_agent(self, app, template, payload, task_id):
        """创建agent实例并附加到运行时"""
        # 4. 创建 AppAgentFactory 并创建 agent 实例
        factory = AppAgentFactory(
            appid=str(app.id),
            template_code=template.template_code,
            app_config=app.config or {},
        )
        agent_instance = factory.create(payload.model_dump())

        # 5. 附加到运行时
        self.runtime.attach(task_id, agent_instance)
        return agent_instance

    async def _execute(self, agent_instance, payload: TextInput, task_id):
        """
        执行agent流式处理并收集结果

        Args:
            agent_instance: Agent 实例
            payload: 任务载荷数据
            task_id: 任务 ID

        Returns:
            包含完整结果和块数的字典
        """
        # 验证 text_message 类型
        logger.info(f"Executing agent stream for task {task_id}")
        logger.info(f"Payload type: {type(payload)}")
        logger.info(f"Payload content: {payload}")

        collected_chunks = []
        async for chunk in agent_instance.stream(payload.model_dump()):
            collected_chunks.append(chunk)
            await self.publish_event(task_id, {"event": "chunk", "data": chunk})

        # 6. 收集完整结果
        full_result = "".join(collected_chunks) if collected_chunks else ""
        async with get_session("aiwen") as db_session:
            message_crud = MessageCRUD(db_session)
            await message_crud.create(
                app_id=str(payload.app_id),
                task_id=task_id,
                query=payload.query,
                answer=full_result,
                conversation_id=str(payload.conversation_id),
                status="finished",
                from_source="api",
                message_content={"human": payload.query, "assitant": full_result},
            )
        return {"answer": full_result, "chunks_count": len(collected_chunks)}

    async def _handle_task_error(self, task_id, error_msg):
        """处理任务错误"""
        try:
            # 获取数据库会话来更新任务状态
            async with get_session("aiwen") as db_session:
                task_crud = AgentTaskCRUD(db_session)

            try:
                await task_crud.update_agent_task_status(
                    task_id, "failed", error=error_msg
                )
                await self.publish_event(
                    task_id, {"event": "failed", "data": error_msg}
                )
                logger.error(f"Task {task_id} failed: {error_msg}")
            except Exception as e:
                logger.error(f"Task {task_id} failed (Exception): {e}")
        except Exception as e:
            logger.error(f"Error updating task status for {task_id}: {e}")

    async def publish_event(self, task_id: UUID, event_data: dict[str, Any]):
        """
        发布任务事件到 Redis Stream.

        Args:
            task_id: 任务 ID
            event_data: 事件数据 (包含 event 和 data 字段)
        """
        try:
            # 使用与 AgentTaskConsumer 一致的 Stream 名称格式
            stream_name = build_stream_name_key(str(task_id), self.task_name)

            # 准备 Stream 消息字段
            fields = {
                "event": event_data.get("event", "chunk"),
                "data": json_dumps(event_data.get("data", "")),
            }

            # 使用 XADD 发布到 Stream
            await self.redis_client.xadd(
                name=stream_name,
                fields=fields,
                maxlen=1000,  # 限制每个任务 Stream 的最大长度
                approximate=True,
            )

            logger.debug(f"Published event '{fields['event']}' to stream {stream_name}")
        except Exception as e:
            logger.error(f"Error publishing event for task {task_id}: {e}")

    def get_running_agent(self, task_id: UUID):
        """
        获取正在运行的 agent 实例.

        Args:
            task_id: 任务 ID

        Returns:
            Agent 实例或 None
        """
        try:
            return self.runtime.get(task_id)
        except KeyError:
            return None

    def get_running_task_count(self) -> int:
        """
        获取正在运行的任务数量.

        Returns:
            运行中的任务数量
        """
        return len(self.runtime._instances)

    async def _handle_cancel_message(self, message_data):
        """
        处理取消消息.

        Args:
            message_data: Redis 消息数据 (bytes 或 str)
        """
        try:
            # 解析消息数据
            data = json.loads(
                message_data.decode()
                if isinstance(message_data, bytes)
                else message_data
            )
            task_id = UUID(data.get("task_id"))

            logger.info(f"Received cancellation request for task {task_id}")

            # 取消任务
            success = await self.cancel_task(task_id)

            if success:
                logger.info(f"Successfully cancelled task {task_id}")
            else:
                logger.warning(f"Task {task_id} not found or already completed")

        except Exception as e:
            logger.error(f"Error handling cancel message: {e}", exc_info=True)

    async def cancel_task(self, task_id: UUID) -> bool:
        """
        取消正在运行的任务.

        Args:
            task_id: 任务 ID

        Returns:
            是否成功取消
        """
        agent = self.get_running_agent(task_id)
        if agent is None:
            logger.warning(f"Task {task_id} not found in runtime")
            return False

        try:
            # 释放 agent 实例
            self.runtime.release(task_id)

            # 发送取消事件
            await self.publish_event(
                task_id, {"event": "cancelled", "data": "Task was cancelled"}
            )

            logger.info(f"Task {task_id} cancelled successfully")
            return True
        except Exception as e:
            logger.error(f"Error cancelling task {task_id}: {e}")
            return False

    async def cleanup(self):
        """
        清理所有资源.
        """
        try:
            # 清理所有运行中的 agent 实例
            task_ids = list(self.runtime._instances.keys())
            for task_id in task_ids:
                self.runtime.release(task_id)
                logger.info(f"Released agent instance for task {task_id}")

            logger.info("AgentWorker cleanup completed")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}", exc_info=True)


async def start_worker(redis_client: redis_async.Redis, db: AsyncSession):
    """
    启动 agent worker 并支持优雅关闭.

    args:
        redis_client: redis 异步客户端
        db_session: 数据库会话工厂

    usage:
        ```python
        import asyncio
        from aiwen.workers.agent_worker import start_worker
        from aiwen.extensions.database import get_session
        from aiwen.middleware.cache_middleware import get_redis_client

        async def main():
            redis_client = get_redis_client(is_async=true)
            db_factory = get_session

            try:
                await start_worker(redis_client, db_factory)
            except KeyboardInterrupt:
                print("worker stopped by user")

        asyncio.run(main())
        ```
    """
    worker = AgentWorker(
        redis_client,
    )

    try:
        await worker.start()
    except KeyboardInterrupt:
        logger.info("worker interrupted by user")
    finally:
        await worker.cleanup()
        logger.info("worker shutdown completed")
