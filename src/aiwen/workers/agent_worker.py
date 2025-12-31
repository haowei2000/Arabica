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
        - 修复 agent_instance.payload 属性不存在的错误
        - 将 payload 作为参数传递给 stream 方法

    v1.2.0 (2025/12/30):
        - 适配新的 Redis 消息格式（app_id 在 payload 内部）
        - 增强消息解析的错误处理和验证

公司名称: 艾普工华(武汉)有限责任公司
版权信息: © 2025 艾普工华(武汉)有限责任公司. 保留所有权利.

依赖模块:
    - redis.asyncio: Redis 异步客户端
    - aiwen.services.agents: Agent 管理和运行时
"""
# aiwen/workers/agent_worker.py
import asyncio
import json
import logging
from typing import Any
from uuid import UUID

import redis.asyncio as redis_async
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.extensions.database import get_session
from aiwen.services.agents.agent_registry import AgentRegistry
from aiwen.services.agents.app_factory import AppAgentFactory
from aiwen.services.agents.crud.agent_template_crud import AgentTemplateCRUD
from aiwen.services.agents.crud.app_crud import AppCRUD
from aiwen.services.agents.crud.task_crud import TaskCRUD
from aiwen.services.agents.runtime import AgentRuntime
from aiwen.utils.json_utils import dumps as json_dumps

logger = logging.getLogger(__name__)


class AgentWorker:
    """
    Worker for processing agent tasks asynchronously.

    使用 AgentRuntime 管理 agent 实例生命周期，支持流式输出和任务状态跟踪。
    """

    def __init__(self, redis_client: redis_async.Redis, db: AsyncSession = None):
        """
        初始化 Agent Worker.

        Args:
            redis_client: Redis 异步客户端
            db: 数据库会话工厂
        """
        self.redis_client = redis_client
        self.db = db or get_session("aiwen")  # 如果没有提供，则使用默认的aiwen数据库会话
        self.runtime = AgentRuntime()  # Agent 运行时管理器
        self.pubsub = None

    async def start(self):
        logger.info("Starting AgentWorker...")
        self.pubsub = self.redis_client.pubsub()
        await self.pubsub.subscribe("agent_tasks")

        try:
            async for message in self.pubsub.listen():
                if message["type"] == "message":
                    try:
                        await self.process_task(message["data"])
                    except Exception as e:
                        logger.error(f"Error processing task: {e}", exc_info=True)
        except asyncio.CancelledError:
            logger.info("AgentWorker cancelled")
        except Exception as e:
            logger.error(f"AgentWorker error: {e}", exc_info=True)
        finally:
            await self.pubsub.unsubscribe("agent_tasks")

    async def process_task(self, message_data):
        """
        处理单个 agent 任务.

        Args:
            message_data: Redis 消息数据 (bytes 或 str)
        """
        task_id = None

        try:
            # 解析消息数据
            data = self._parse_message_data(message_data)
            task_id = data["task_id"]
            app_id = data["app_id"]
            payload = data.get("payload", {})

            logger.info(f"Processing task {task_id} for app {app_id}")

            # 获取数据库会话

            task_crud = TaskCRUD(self.db)
            # 更新任务状态为 running
            await task_crud.update_task_status(task_id, "running")

            # 执行任务的主要逻辑
            await self._execute_task_logic(task_id, app_id, payload, task_crud)
            await self.db.commit()
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in task message: {e}")
        except Exception as e:
            logger.error(f"Error in process_task: {e}", exc_info=True)
            # 如果有 task_id，确保更新任务状态和释放资源
            if task_id:
                await self._handle_task_error(task_id, str(e))

    def _parse_message_data(self, message_data):
        """
        解析消息数据并返回任务相关信息

        新格式 (v1.2.0+): app_id 在 payload 内部
        {
            "task_id": "...",
            "payload": {
                "app_id": "...",
                ...
            }
        }

        Raises:
            ValueError: 如果消息格式无效或缺少必需字段
        """
        # 解析 JSON 数据
        data = json.loads(message_data.decode() if isinstance(message_data, bytes) else message_data)

        # 详细日志记录
        logger.info(f"Received message data: {data}")
        logger.info(f"Data type: {type(data)}")

        payload = data.get("payload", {})
        logger.info(f"Extracted payload: {payload}")
        logger.info(f"Payload type: {type(payload)}")

        # Validate required fields
        if "task_id" not in data:
            raise ValueError("Missing required field: task_id")

        app_id = payload.get("app_id")
        if not app_id:
            raise ValueError(
                "Missing required field: app_id in payload. "
                "Ensure ChatService is using the new payload format (v1.2.0+)"
            )

        parsed_result = {
            "task_id": UUID(data["task_id"]),
            "app_id": app_id,
            "payload": payload
        }
        logger.info(f"Parsed result: {parsed_result}")

        return parsed_result

    async def _execute_task_logic(self, task_id, app_id, payload, task_crud):
        """执行任务的主要逻辑"""
        try:
            # 获取数据库会话
            app_crud = AppCRUD(self.db)
            template_crud = AgentTemplateCRUD(self.db)

            try:
                # 1. 从数据库获取 app 配置
                app = await self._get_app_by_id(app_crud, app_id)

                # 2. 获取 agent 模板
                template = await self._get_agent_template(template_crud, app)

                # 3. 检查模板代码是否在 Registry 中注册
                await self._validate_template_registration(template)

                # 4. 创建 agent 实例并附加到运行时
                agent_instance = await self._create_and_attach_agent(app, template, payload, task_id)

                logger.info(f"Agent instance created for task {task_id}, type: {template.template_code}")

                # 5. 流式执行 agent 并发送事件
                result_data = await self._execute_agent_stream(agent_instance, payload, task_id)

                # 6. 更新任务状态为成功
                await task_crud.update_task_status(task_id, "success", result=result_data)
                await self.publish_event(task_id, {"event": "success", "data": json_dumps(result_data)})

                logger.info(f"Task {task_id} completed successfully with {len(result_data.get('chunks', []))} chunks")

            finally:
                # 8. 释放 agent 实例
                self.runtime.release(task_id)
                logger.debug(f"Agent instance released for task {task_id}")

        except ValueError as e:
            await self._handle_task_error(task_id, str(e))
        except Exception as e:
            logger.error(f"Task {task_id} failed (Exception): {e}", exc_info=True)
            await self._handle_task_error(task_id, str(e))

    async def _get_app_by_id(self, app_crud, app_id):
        """根据ID获取app配置"""
        app = await app_crud.get_app_by_id(UUID(app_id) if isinstance(app_id, str) else app_id)
        if not app:
            raise ValueError(f"App with ID '{app_id}' not found")
        return app

    async def _get_agent_template(self, template_crud, app):
        """获取agent模板"""
        if not app.agent_template_id:
            raise ValueError("App has no associated agent template")

        template = await template_crud.get_template_by_id(app.agent_template_id)
        if not template:
            raise ValueError(f"Agent template '{app.agent_template_id}' not found")
        return template

    async def _validate_template_registration(self, template):
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

    async def _create_and_attach_agent(self, app, template, payload, task_id):
        """创建agent实例并附加到运行时"""
        # 4. 创建 AppAgentFactory 并创建 agent 实例
        factory = AppAgentFactory(
            appid=str(app.id),
            template_code=template.template_code,
            app_config=app.config or {}
        )
        agent_instance = factory.create(payload)

        # 5. 附加到运行时
        self.runtime.attach(task_id, agent_instance)
        return agent_instance

    async def _execute_agent_stream(self, agent_instance, payload, task_id):
        """
        执行agent流式处理并收集结果

        Args:
            agent_instance: Agent 实例
            payload: 任务载荷数据
            task_id: 任务 ID

        Returns:
            包含完整结果和块数的字典
        """
        # 验证 payload 类型
        logger.info(f"Executing agent stream for task {task_id}")
        logger.info(f"Payload type: {type(payload)}")
        logger.info(f"Payload content: {payload}")

        # 确保 payload 是字典
        if not isinstance(payload, dict):
            error_msg = (
                f"Invalid payload type: expected dict, got {type(payload).__name__}. "
                f"Payload content: {payload}"
            )
            logger.error(error_msg)
            raise TypeError(error_msg)

        collected_chunks = []
        async for chunk in agent_instance.stream(payload):
            collected_chunks.append(chunk)
            await self.publish_event(task_id, {"event": "chunk", "data": chunk})

        # 6. 收集完整结果
        full_result = "".join(collected_chunks) if collected_chunks else ""
        return {"answer": full_result, "chunks_count": len(collected_chunks)}

    async def _handle_task_error(self, task_id, error_msg):
        """处理任务错误"""
        try:
            # 获取数据库会话来更新任务状态
            session_gen, db_session = await self._get_db_session()
            task_crud = TaskCRUD(db_session)

            try:
                await task_crud.update_task_status(task_id, "failed", error=error_msg)
                await self.publish_event(task_id, {"event": "failed", "data": error_msg})
                logger.error(f"Task {task_id} failed: {error_msg}")
            finally:
                await self._close_db_session(session_gen)
        except Exception as e:
            logger.error(f"Error updating task status for {task_id}: {e}")

    async def _close_db_session(self, session_gen):
        """关闭数据库会话"""
        try:
            await session_gen.__anext__()  # 触发 finally 块
        except StopAsyncIteration:
            pass  # 正常结束
        except Exception as e:
            logger.error(f"Error closing database session: {e}")

    async def publish_event(self, task_id: UUID, event_data: dict[str, Any]):
        """
        发布任务事件到 Redis.

        Args:
            task_id: 任务 ID
            event_data: 事件数据
        """
        try:
            channel = f"agent:task:{task_id}"
            await self.redis_client.publish(channel, json_dumps(event_data))
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
            await self.publish_event(task_id, {"event": "cancelled", "data": "Task was cancelled"})

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

            # 关闭 pubsub 连接
            if self.pubsub:
                await self.pubsub.unsubscribe("agent_tasks")
                await self.pubsub.close()

            logger.info("AgentWorker cleanup completed")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}", exc_info=True)


async def start_worker(redis_client: redis_async.Redis, db: AsyncSession):
    """
    启动 agent worker 并支持优雅关闭.

    args:
        redis_client: redis 异步客户端
        db: 数据库会话工厂

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
    worker = AgentWorker(redis_client, db)

    try:
        await worker.start()
    except asyncio.cancellederror:
        logger.info("worker received cancellation signal")
    except KeyboardInterrupt:
        logger.info("worker interrupted by user")
    finally:
        await worker.cleanup()
        logger.info("worker shutdown completed")
