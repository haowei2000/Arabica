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

from aiwen.models.app import App
from aiwen.models.runs.run import Run
from aiwen.registries import ExecutorRegistry
from aiwen.schemas.events.event_payloads import UserMessage
from aiwen.services.events.event_publisher import EventPublisher
from aiwen.registries.base_class.base_executor import AgentEvent, Executor
from aiwen.services.executor.runtime import AgentRuntime
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

    def _parse_redis_event(self, event_data: dict[bytes, bytes]) -> dict:
        """Parse Redis stream event data.

        Redis streams return data as bytes, and JSON fields are serialized strings.
        This method converts them back to proper Python types.

        Args:
            event_data: Raw event data from Redis stream

        Returns:
            Parsed event data as a dict
        """
        # Decode bytes to strings
        decoded = {
            k.decode() if isinstance(k, bytes) else k:
            v.decode() if isinstance(v, bytes) else v
            for k, v in event_data.items()
        }

        # Parse JSON payload back to dict
        if "payload" in decoded and decoded["payload"]:
            try:
                decoded["payload"] = json.loads(decoded["payload"])
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse payload JSON: {decoded['payload']}")
                decoded["payload"] = {}

        return decoded

    async def _ensure_consumer_group(self, stream_name: str) -> None:
        """Ensure consumer group exists, create if not."""
        try:
            await self.redis.xgroup_create(
                stream_name,
                CONSUMER_GROUP,
                id="0",
                mkstream=True,
            )
            logger.info(
                f"Created consumer group '{CONSUMER_GROUP}' for stream '{stream_name}'"
            )
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
                redis_messages = await self.redis.xreadgroup(
                    groupname=CONSUMER_GROUP,
                    consumername=self.consumer_name,
                    streams={stream_name: ">"},
                    count=1,
                    block=1000,
                )

                if not redis_messages:
                    continue

                _, event_queue = redis_messages[0]
                for event_id, event_data in event_queue:
                    try:
                        # Parse Redis stream data (bytes to strings, JSON to dicts)
                        parsed_event = self._parse_redis_event(event_data)
                        await self.handle_event(parsed_event)
                        # 成功处理后确认消息
                        await self.redis.xack(stream_name, CONSUMER_GROUP, event_id)
                    except Exception as e:
                        logger.error(
                            f"Failed to process message {event_id}: {e}",
                            exc_info=True,
                        )
                        # 注意：失败的消息不会被确认，会进入 pending 队列等待重试

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Worker stream read error: {e}", exc_info=True)
                await asyncio.sleep(1)

    def prepare_executor(
        self, executor_code: str, app_config: dict | None = None
    ) -> Executor:
        """
        根据 executor_code 获取 Executor 类，并准备配置。

        Args:
            executor_code: Executor 的唯一标识符
            app_config: 应用级别的配置，会覆盖模板配置

        Returns:
            配置好的 Executor 实例

        Raises:
            ValueError: 当 executor_code 未找到时
        """
        executor_cls = ExecutorRegistry.get(executor_code)
        if not executor_cls:
            raise ValueError(f"Executor with code '{executor_code}' not found")

        # 获取模板配置
        config: dict = {}
        template_config = executor_cls.TEMPLATE.get("config")
        if template_config is not None:
            if hasattr(template_config, "model_dump"):
                config = template_config.model_dump()
            elif isinstance(template_config, dict):
                config = dict(template_config)

        # 应用级配置覆盖模板配置
        if app_config:
            config.update(app_config)

        return executor_cls(config)

    async def handle_event(self, event: dict):
        """
        处理来自 run_tasks stream 的消息。

        Args:
            event: Parsed event data from Redis stream
        """
        run_id = None
        try:
            # 1. 验证事件数据
            if not event.get("run_id"):
                logger.warning("Received message without run_id, skipping")
                return

            if not event.get("executor_code"):
                logger.error(
                    "Received message without executor_code, cannot determine executor"
                )
                return

            run_id = UUID(str(event["run_id"]))
            input_data = event.get("payload") or {}

            # 2. 获取 Run 及相关数据
            run, app_config = await self._fetch_run_data(run_id)
            if not run:
                raise ValueError(f"Run {run_id} not found")

            if RunStateMachine.is_terminal(run.status):
                logger.warning(f"Run {run_id} is terminal, skipping execution")
                return

            # 3. 准备 Executor
            executor = self.prepare_executor(event["executor_code"], app_config)
            self.runtime.attach(run_id, executor)

            # 4. 根据 Run 状态执行相应操作
            workspace_id = str(run.workspace_id)
            await self._handle_run_by_status(
                executor, run, input_data, run_id, workspace_id
            )

        except Exception as e:
            logger.error(f"handle_message error: {e}", exc_info=True)
            if run_id:
                try:
                    await self.state_machine.fail(
                        run_id, error=str(e), auto_commit=True
                    )
                except Exception as fail_err:
                    logger.error(f"Failed to mark run {run_id} as failed: {fail_err}")

    async def _fetch_run_data(self, run_id: UUID) -> tuple[Run | None, dict | None]:
        """
        获取 Run 和对应的 App 配置。

        Args:
            run_id: Run 的 UUID

        Returns:
            (Run对象, App配置字典) 的元组
        """
        stmt_run = await self.db.execute(select(Run).where(Run.id == str(run_id)))
        run = stmt_run.scalar_one_or_none()
        if not run:
            return None, None

        app_config = None
        if run.app_id:
            app_result = await self.db.execute(
                select(App).where(App.id == str(run.app_id))
            )
            app = app_result.scalar_one_or_none()
            if app and app.config:
                app_config = app.config

        return run, app_config

    async def _handle_run_by_status(
        self,
        executor: Executor,
        run: Run,
        user_message: UserMessage,
        run_id: UUID,
        workspace_id: str,
    ):
        """
        根据 Run 状态分发执行逻辑。

        Args:
            executor: Executor 实例
            run: Run 对象
            user_message: 输入数据
            run_id: Run UUID
            workspace_id: 工作区 ID
        """
        run_status = run.status

        if run_status == RunStatus.PENDING.value:
            await self.state_machine.start(
                run_id, triggered_by="user", auto_commit=True
            )
            await self._execute_run(executor, user_message, run_id, workspace_id)

        elif run_status == RunStatus.WAITING.value:
            # TODO 处理恢复场景：获取审批信息
            # user_message = await self._prepare_resume_data(run_id, run.waiting_for, user_message)  # noqa: ERA001
            await self.state_machine.resume_from_tool(run_id, auto_commit=True)
            await self._execute_run(executor, user_message, run_id, workspace_id)

        elif run_status == RunStatus.RUNNING.value:
            await self._execute_run(executor, user_message, run_id, workspace_id)

    async def _prepare_resume_data(
        self, run_id: UUID, waiting_for: dict | None, user_message: UserMessage
    ) -> dict:
        """
        准备恢复执行所需的数据（从 Redis 获取审批信息）。

        Args:
            run_id: Run UUID
            waiting_for: 等待的工具信息
            input_data: 原始输入数据

        Returns:
            包含恢复信息的输入数据
        """
        resume_key = f"run:{run_id}:resume_approval"
        raw_approval = await self.redis.get(resume_key)
        approval_data = json.loads(raw_approval) if raw_approval else {}

        if raw_approval:
            await self.redis.delete(resume_key)

        return {
            **user_message,
            "_resumed": True,
            "_waiting_info": waiting_for or {},
            "_approval": {
                "approved": approval_data.get("approval", True),
                "tool_result": approval_data.get("tool_result"),
                "user_input": approval_data.get("user_input"),
            },
        }

    async def _execute_run(
        self,
        executor: Executor,
        user_message: UserMessage,
        run_id: UUID,
        workspace_id: str,
    ):
        """
        执行 Agent 并通过 EventPublisher 发布所有事件。
        """
        event_count = 0
        check_interval = 10  # 每处理 10 个事件检查一次取消状态

        try:
            async for event in executor.stream(user_message):
                # 通过 EventPublisher 发布事件（统一格式）
                await self._publish_agent_event(event, run_id, workspace_id)

                # 定期检查是否被取消（减少数据库查询频率）
                event_count += 1
                # 合并条件检查，一次性判断是否需要检查取消状态
                if event_count % check_interval == 0 and await self._is_run_cancelled(
                    run_id
                ):
                    logger.info(f"Run {run_id} cancelled, stopping agent")
                    await executor.cancel()
                    return

            # 执行完成
            await self.state_machine.complete(
                run_id, output_data={"output": "Run completed"}, auto_commit=True
            )
            logger.info(f"Run {run_id} completed")

        except executor.WaitingForTool as e:
            await self.state_machine.pause_for_tool(
                run_id, waiting_for=e.info, auto_commit=True
            )
            logger.info(f"Run {run_id} paused for tool {e.info}")
        except Exception as e:
            logger.error(f"Executor failed for run {run_id}: {e}", exc_info=True)
            await self.state_machine.fail(run_id, error=str(e), auto_commit=True)
        finally:
            self.runtime.release(run_id)

    async def _is_run_cancelled(self, run_id: UUID) -> bool:
        """
        检查 Run 是否被取消。

        Args:
            run_id: Run UUID

        Returns:
            如果 Run 被取消返回 True，否则返回 False
        """
        try:
            result = await self.db.execute(
                select(Run.status).where(Run.id == str(run_id))
            )
            status = result.scalar_one_or_none()
            return status == RunStatus.CANCELLED.value
        except Exception as e:
            logger.warning(f"Failed to check run status for {run_id}: {e}")
            return False

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
