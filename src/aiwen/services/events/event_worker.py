# aiwen/workers/executor_worker.py
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
from aiwen.services.agent.base import Executor
from aiwen.services.agent.runtime import AgentRuntime
from aiwen.services.runs.run_state_machine import RunStateMachine, RunStatus

logger = logging.getLogger(__name__)

# Redis stream the worker polls for user.message events.
# EventPublisher publishes USER_MESSAGE events to this stream.
RUN_STREAM = "run_tasks"

# Consumer group name for reliable message processing
CONSUMER_GROUP = "run_workers"


class Worker:
    """
    Worker: 消费 Redis Stream 的 Run 任务，调度对应 Executor。

    Uses Redis Consumer Groups for reliable at-least-once delivery.
    """

    def __init__(self, redis_client: redis_async.Redis, db: AsyncSession, consumer_name: str = "worker-1"):
        self.redis = redis_client
        self.db = db
        self.consumer_name = consumer_name
        self.runtime = AgentRuntime()  # 管理活跃 Executor 实例
        # self.workspace = WorkspaceManager()  # 管理 workspace 上下文 - 当前未实现
        self.state_machine = RunStateMachine(db, redis_client)

    async def _ensure_consumer_group(self, stream_name: str) -> None:
        """Ensure consumer group exists, create if not."""
        try:
            await self.redis.xgroup_create(
                stream_name,
                CONSUMER_GROUP,
                id="0",  # Start from the beginning of the stream
                mkstream=True,  # Create stream if it doesn't exist
            )
            logger.info(f"Created consumer group '{CONSUMER_GROUP}' for stream '{stream_name}'")
        except Exception as e:
            # BUSYGROUP means group already exists, which is fine
            if "BUSYGROUP" not in str(e):
                raise
            logger.debug(f"Consumer group '{CONSUMER_GROUP}' already exists")

    async def start(self, stream_name: str):
        """Start consuming messages from the stream using consumer groups."""
        await self._ensure_consumer_group(stream_name)
        logger.info(f"Worker '{self.consumer_name}' listening on stream: {stream_name}")

        while True:
            try:
                # Use XREADGROUP for reliable message processing
                # ">" means only undelivered messages
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
                        # Acknowledge successful processing
                        await self.redis.xack(stream_name, CONSUMER_GROUP, message_id)
                    except Exception as e:
                        logger.error(f"Failed to process message {message_id}: {e}", exc_info=True)
                        # Don't ACK - message will be redelivered to another consumer

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Worker stream read error: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def handle_message(self, message_data: dict):
        """
        处理来自 run_tasks stream 的消息。

        支持两种消息格式:

        1. EventPublisher user.message 事件:
        {
            "id": event_id,
            "event_type": "user.message",
            "run_id": str,
            "payload": json (包含 executor_code, input_data),
            ...
        }

        2. Resume 消息 (直接发布):
        {
            "run_id": str,
            "executor_code": str,
            "input": json,
            "triggered_by": "resume",
        }
        """
        try:
            # 解析 run_id
            run_id_str = message_data.get("run_id")
            if not run_id_str:
                logger.warning("Received message without run_id, skipping")
                return

            run_id = UUID(run_id_str)

            # 判断消息格式并解析
            if "event_type" in message_data:
                # 格式1: EventPublisher 事件
                payload_str = message_data.get("payload", "{}")
                if isinstance(payload_str, bytes):
                    payload_str = payload_str.decode()
                payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str

                executor_code = payload.get("executor_code", "DEFAULT001")
                input_data = payload.get("input_data", {})
                triggered_by = "user"
            else:
                # 格式2: 直接发布的 resume 消息
                executor_code = message_data.get("executor_code", "DEFAULT001")
                input_data = message_data.get("input", {})
                triggered_by = message_data.get("triggered_by", "user")

            executor_cls = AgentRegistry.get(executor_code)

            # ── parse & normalise input ────────────────────────
            if isinstance(input_data, (str, bytes)):
                input_data = json.loads(input_data)
            # Map API field name → TextInput schema field
            if "message" in input_data and "query" not in input_data:
                input_data["query"] = input_data.pop("message")

            # 根据 Run 状态决定行为
            async with self.db.begin():
                stmt_run = await self.state_machine.db.execute(
                    select(Run).where(Run.id == str(run_id))
                )
                run = stmt_run.scalar_one_or_none()
                if not run:
                    raise ValueError(f"Run {run_id} not found")

                if RunStateMachine.is_terminal(run.status):
                    logger.warning(f"Run {run_id} is terminal, skipping execution")
                    return

                # ── build executor config ──────────────────────
                # Template defaults first; app-specific overrides on top.
                config: dict = {}
                template_config = executor_cls.TEMPLATE.get("config")
                if template_config is not None:
                    if hasattr(template_config, "model_dump"):
                        config = template_config.model_dump()
                    elif isinstance(template_config, dict):
                        config = dict(template_config)
                if run.app_id:
                    app_result = await self.db.execute(
                        select(App).where(App.id == run.app_id)
                    )
                    app = app_result.scalar_one_or_none()
                    if app and app.config:
                        config.update(app.config)

                executor: Executor = executor_cls(config)
                self.runtime.attach(run_id, executor)

                # 启动或恢复
                if run.status == RunStatus.PENDING.value:
                    await self.state_machine.start(run_id, triggered_by=triggered_by)
                    await self._execute_run(executor, input_data, run_id)
                elif run.status == RunStatus.WAITING.value:
                    # ── HITL resume ──────────────────────────────
                    # Load the approval decision the resume endpoint stored
                    resume_key = f"run:{run_id}:resume_approval"
                    raw_approval = await self.redis.get(resume_key)
                    approval_data = json.loads(raw_approval) if raw_approval else {}
                    if raw_approval:
                        await self.redis.delete(resume_key)

                    # Merge resume metadata into input so the executor
                    # can reconstruct messages at the interruption point.
                    input_data = {
                        **input_data,
                        "_resumed": True,
                        "_waiting_info": run.waiting_for or {},
                        "_approval": {
                            "approved": approval_data.get("approval", True),
                            "tool_result": approval_data.get("tool_result"),
                            "user_input": approval_data.get("user_input"),
                        },
                    }
                    await self.state_machine.resume_from_tool(run_id)
                    await self._execute_run(executor, input_data, run_id)
                elif run.status == RunStatus.RUNNING.value:
                    # 恢复中断 run
                    await self._execute_run(executor, input_data, run_id)

        except Exception as e:
            logger.error(f"handle_message error: {e}", exc_info=True)
            try:
                run_id = UUID(message_data.get("run_id"))
                await self.state_machine.fail(run_id, error=str(e))
            except Exception:
                pass

    async def _execute_run(self, executor: Executor, input_data: dict, run_id: UUID):
        """
        核心执行流程，支持 token 流式输出、工具等待、取消。
        """
        try:
            # 流式输出
            async for token in executor.stream(input_data):
                # token 发布事件
                await self._publish_token(run_id, {"type": "chunk", "data": token})

                # 增量 push workspace 上下文
                # await self.workspace.push(run_id, token)  # 当前未实现此功能

                # 检查 run 状态是否被 cancel
                # 由于使用的是SQLAlchemy异步会话，我们需要通过查询获取最新的run状态
                result = await self.state_machine.db.execute(select(Run).where(Run.id == str(run_id)))
                run = result.scalar_one_or_none()
                if run and run.status == RunStatus.CANCELLED.value:
                    logger.info(f"Run {run_id} cancelled, stopping agent")
                    await executor.cancel()
                    return

            # 执行完成
            await self.state_machine.complete(run_id, output_data={"output": "Run completed"})
            logger.info(f"Run {run_id} completed")

        except executor.WaitingForTool as e:
            # 工具等待
            await self.state_machine.pause_for_tool(run_id, waiting_for=e.info)
            logger.info(f"Run {run_id} paused for tool {e.info}")
        except Exception as e:
            logger.error(f"Executor failed for run {run_id}: {e}", exc_info=True)
            await self.state_machine.fail(run_id, error=str(e))
        finally:
            self.runtime.release(run_id)

    async def _publish_token(self, run_id: UUID, token: dict):
        """
        token 格式: {"type": "text|tool|chunk", "data": "..."}
        发布 token 到 Redis Stream
        """
        stream_name = f"run:{run_id}:tokens"
        try:
            await self.redis.xadd(
                stream_name,
                fields={
                    "type": token.get("type", "chunk"),
                    "data": str(token.get("data", "")),
                },
                maxlen=1000,
                approximate=True,
            )
        except Exception as e:
            logger.error(f"Error publishing token for run {run_id}: {e}")
