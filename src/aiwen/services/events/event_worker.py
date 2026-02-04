# aiwen/workers/executor_worker.py
"""
Event Worker - 事件驱动的 Agent 执行器

监听 run_tasks stream，执行 Agent 并通过 EventPublisher 发布所有事件。
所有事件使用统一的 EventPublisher 格式，前端只需处理一种结构。
"""
import asyncio
import json
import logging
from uuid import UUID

import redis.asyncio as redis_async
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.agents.app import App
from aiwen.models.agents.run import Run
from aiwen.services.agent.agent_registry import AgentRegistry
from aiwen.services.agent.base import AgentEvent, Executor
from aiwen.services.agent.runtime import AgentRuntime
from aiwen.services.events.event_publisher import EventPublisher
from aiwen.services.runs.run_state_machine import RunStateMachine, RunStatus

logger = logging.getLogger(__name__)

# Redis stream the worker polls for user.message events.
RUN_STREAM = "run_tasks"

# Consumer group name for reliable message processing
CONSUMER_GROUP = "run_workers"


class Worker:
    """
    Worker: 消费 Redis Stream 的 Run 任务，调度对应 Executor。

    所有事件通过 EventPublisher 发布，保证格式统一。
    """

    def __init__(
        self,
        redis_client: redis_async.Redis,
        db: AsyncSession,
        consumer_name: str = "worker-1",
    ):
        self.redis = redis_client
        self.db = db
        self.consumer_name = consumer_name
        self.runtime = AgentRuntime()
        self.state_machine = RunStateMachine(db, redis_client)
        # EventPublisher for unified event format
        self.event_publisher = EventPublisher(db, redis_client)

    async def _ensure_consumer_group(self, stream_name: str) -> None:
        """Ensure consumer group exists, create if not."""
        try:
            await self.redis.xgroup_create(
                stream_name,
                CONSUMER_GROUP,
                id="0",
                mkstream=True,
            )
            logger.info(f"Created consumer group '{CONSUMER_GROUP}' for stream '{stream_name}'")
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise
            logger.debug(f"Consumer group '{CONSUMER_GROUP}' already exists")

    async def start(self, stream_name: str):
        """Start consuming messages from the stream using consumer groups."""
        await self._ensure_consumer_group(stream_name)
        logger.info(f"Worker '{self.consumer_name}' listening on stream: {stream_name}")

        while True:
            try:
                messages = await self.redis.xreadgroup(
                    groupname=CONSUMER_GROUP,
                    consumername=self.consumer_name,
                    streams={stream_name: ">"},
                    count=1,
                    block=1000,
                )

                if not messages:
                    continue

                _, msg_list = messages[0]
                for message_id, data in msg_list:
                    try:
                        await self.handle_message(data)
                        await self.redis.xack(stream_name, CONSUMER_GROUP, message_id)
                    except Exception as e:
                        logger.error(f"Failed to process message {message_id}: {e}", exc_info=True)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Worker stream read error: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def handle_message(self, message_data: dict):
        """
        处理来自 run_tasks stream 的消息。
        """
        run_id: UUID | None = None
        executor: Executor | None = None
        workspace_id: str | None = None

        try:
            run_id_str = message_data.get("run_id")
            if not run_id_str:
                logger.warning("Received message without run_id, skipping")
                return

            run_id = UUID(run_id_str)

            # 解析消息格式
            if "event_type" in message_data:
                # EventPublisher 事件格式
                payload_str = message_data.get("payload", "{}")
                if isinstance(payload_str, bytes):
                    payload_str = payload_str.decode()
                payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str

                executor_code = payload.get("executor_code", "DEFAULT001")
                input_data = payload.get("input_data", {})
                triggered_by = "user"
            else:
                # Resume 消息格式
                executor_code = message_data.get("executor_code", "DEFAULT001")
                input_data = message_data.get("input", {})
                triggered_by = message_data.get("triggered_by", "user")

            executor_cls = AgentRegistry.get(executor_code)

            # 解析 input
            if isinstance(input_data, (str, bytes)):
                input_data = json.loads(input_data)
            if "message" in input_data and "query" not in input_data:
                input_data["query"] = input_data.pop("message")

            # Phase 1: 获取 Run 数据
            run_status: str | None = None
            waiting_for: dict | None = None
            app_id: str | None = None

            # 使用 session 直接查询（auto-begin 模式）
            stmt_run = await self.db.execute(select(Run).where(Run.id == str(run_id)))
            run = stmt_run.scalar_one_or_none()
            if not run:
                raise ValueError(f"Run {run_id} not found")

            if RunStateMachine.is_terminal(run.status):
                logger.warning(f"Run {run_id} is terminal, skipping execution")
                return

            # 提取需要的数据
            run_status = run.status
            workspace_id = run.workspace_id
            waiting_for = run.waiting_for
            app_id = run.app_id

            # 构建 executor 配置
            config: dict = {}
            template_config = executor_cls.TEMPLATE.get("config")
            if template_config is not None:
                if hasattr(template_config, "model_dump"):
                    config = template_config.model_dump()
                elif isinstance(template_config, dict):
                    config = dict(template_config)

            if app_id:
                app_result = await self.db.execute(select(App).where(App.id == app_id))
                app = app_result.scalar_one_or_none()
                if app and app.config:
                    config.update(app.config)

            # Phase 2: 创建 executor 并执行
            executor = executor_cls(config)
            self.runtime.attach(run_id, executor)

            if run_status == RunStatus.PENDING.value:
                await self.state_machine.start(run_id, triggered_by=triggered_by, auto_commit=True)
                await self._execute_run(executor, input_data, run_id, workspace_id)
            elif run_status == RunStatus.WAITING.value:
                resume_key = f"run:{run_id}:resume_approval"
                raw_approval = await self.redis.get(resume_key)
                approval_data = json.loads(raw_approval) if raw_approval else {}
                if raw_approval:
                    await self.redis.delete(resume_key)

                input_data = {
                    **input_data,
                    "_resumed": True,
                    "_waiting_info": waiting_for or {},
                    "_approval": {
                        "approved": approval_data.get("approval", True),
                        "tool_result": approval_data.get("tool_result"),
                        "user_input": approval_data.get("user_input"),
                    },
                }
                await self.state_machine.resume_from_tool(run_id, auto_commit=True)
                await self._execute_run(executor, input_data, run_id, workspace_id)
            elif run_status == RunStatus.RUNNING.value:
                await self._execute_run(executor, input_data, run_id, workspace_id)

        except Exception as e:
            logger.error(f"handle_message error: {e}", exc_info=True)
            if run_id:
                try:
                    await self.state_machine.fail(run_id, error=str(e), auto_commit=True)
                except Exception as fail_err:
                    logger.error(f"Failed to mark run {run_id} as failed: {fail_err}")

    async def _execute_run(
        self,
        executor: Executor,
        input_data: dict,
        run_id: UUID,
        workspace_id: str,
    ):
        """
        执行 Agent 并通过 EventPublisher 发布所有事件。
        """
        try:
            async for event in executor.stream(input_data):
                # 通过 EventPublisher 发布事件（统一格式）
                await self._publish_agent_event(event, run_id, workspace_id)

                # 检查是否被取消
                try:
                    result = await self.db.execute(select(Run).where(Run.id == str(run_id)))
                    run = result.scalar_one_or_none()
                    if run and run.status == RunStatus.CANCELLED.value:
                        logger.info(f"Run {run_id} cancelled, stopping agent")
                        await executor.cancel()
                        return
                except Exception as check_err:
                    logger.warning(f"Failed to check run status: {check_err}")

            # 执行完成
            await self.state_machine.complete(run_id, output_data={"output": "Run completed"}, auto_commit=True)
            logger.info(f"Run {run_id} completed")

        except executor.WaitingForTool as e:
            await self.state_machine.pause_for_tool(run_id, waiting_for=e.info, auto_commit=True)
            logger.info(f"Run {run_id} paused for tool {e.info}")
        except Exception as e:
            logger.error(f"Executor failed for run {run_id}: {e}", exc_info=True)
            await self.state_machine.fail(run_id, error=str(e), auto_commit=True)
        finally:
            self.runtime.release(run_id)

    async def _publish_agent_event(
        self,
        event: AgentEvent,
        run_id: UUID,
        workspace_id: str,
    ):
        """
        通过 EventPublisher 发布 Agent 事件。

        AgentEvent 会被转换为统一的 EventPublisher 格式:
        {
            "id": uuid,
            "event_type": "agent.token" | "agent.message" | "tool.call" | ...,
            "workspace_id": str,
            "run_id": str,
            "payload": {...},
            "sequence": int,
            "created_at": timestamp
        }
        """
        try:
            await self.event_publisher.publish(
                event_type=event.event_type,
                workspace_id=workspace_id,
                run_id=str(run_id),
                payload=event.payload,
                auto_commit=True,
            )
        except Exception as e:
            logger.error(f"Error publishing agent event for run {run_id}: {e}")
