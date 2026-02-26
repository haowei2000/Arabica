# aiwen/workers/executor_worker.py
"""
Event Worker - 事件驱动的 Agent 执行器

监听 run_tasks stream，执行 Agent 并通过 EventPublisher 发布所有事件。
所有事件使用统一的 EventPublisher 格式，前端只需处理一种结构。
"""

import asyncio
from datetime import UTC, datetime
import json
import logging
from uuid import UUID

import redis.asyncio as redis_async
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.config.factory import get_settings
from aiwen.core.enums import EventType
from aiwen.core.interfaces import AgentEvent
from aiwen.core.interfaces.protocols import ExecutorProtocol
from aiwen.models.app import App
from aiwen.models.events.event import Event
from aiwen.models.runs.run import Run
from aiwen.registries.core import ExecutorRegistry
from aiwen.registries.dynamic_loader import DynamicToolLoader
from aiwen.registries.tool_service import RegistryToolCaller, RegistryToolProvider
from aiwen.schemas.events.event_payloads import UserMessage
from aiwen.services.events.event_publisher import EventPublisher
from aiwen.services.executor.runtime import ExecutorInstanceManager
from aiwen.services.runs.run_state_machine import RunStateMachine, RunStatus
from aiwen.services.runs.stuck_run_detector import StuckRunDetector

logger = logging.getLogger(__name__)

_redis_cfg = get_settings().redis
REDIS_CONSUMER_GROUP = _redis_cfg.consumer_group
REDIS_EXECUTOR_LABEL = _redis_cfg.executor_label
REDIS_RUN_LABEL = _redis_cfg.run_label
REDIS_RUN_RESUME_APPROVAL_SUFFIX = _redis_cfg.run_resume_approval_suffix


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
        self.runtime = ExecutorInstanceManager()
        # EventPublisher created once, shared with RunStateMachine
        self.event_publisher = EventPublisher(db, redis_client)
        self.state_machine = RunStateMachine(
            db, redis_client, event_publisher=self.event_publisher
        )

        self._stuck_detector = StuckRunDetector(self.state_machine)
        self._stuck_detector_task: asyncio.Task | None = None

        # ── Startup-initialized tool services (stateless / reusable) ──
        self._tool_caller = RegistryToolCaller()
        self._default_tool_provider = RegistryToolProvider()

    @staticmethod
    def _parse_redis_event(event_data: dict[bytes, bytes]) -> Event:
        """Parse Redis stream event data into an Event instance.

        Args:
            event_data: Raw event data from Redis stream

        Returns:
            A transient Event instance (not attached to the DB session).
        """
        return Event.from_redis_fields(event_data)

    async def _ensure_consumer_group(self, stream_name: str) -> None:
        """Ensure a consumer group exists, create if not."""
        try:
            await self.redis.xgroup_create(
                stream_name,
                REDIS_CONSUMER_GROUP,
                id="0",
                mkstream=True,
            )
            logger.info(
                f"Created consumer group '{REDIS_CONSUMER_GROUP}' for stream '{stream_name}'"
            )
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise
            logger.debug(f"Consumer group '{REDIS_CONSUMER_GROUP}' already exists")

    async def _stuck_run_detector_loop(self) -> None:
        """Periodically detect and recover stuck runs."""
        while True:
            try:
                await asyncio.sleep(120)
                recovered = await self._stuck_detector.detect_and_recover(self.db)
                if recovered:
                    logger.info(f"Stuck detector recovered runs: {recovered}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Stuck run detector error: {e}", exc_info=True)

    async def start(self, stream_name: str):
        """Start consuming messages from the stream using consumer groups."""
        await self._ensure_consumer_group(stream_name)
        logger.info(f"Worker '{self.consumer_name}' listening on stream: {stream_name}")

        # Launch stuck run detector as background task
        self._stuck_detector_task = asyncio.create_task(self._stuck_run_detector_loop())

        while True:
            try:
                redis_messages = await self.redis.xreadgroup(
                    groupname=REDIS_CONSUMER_GROUP,
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
                        await self.redis.xack(
                            stream_name, REDIS_CONSUMER_GROUP, event_id
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to process message {event_id}: {e}",
                            exc_info=True,
                        )
                        # 注意：失败的消息不会被确认，会进入 pending 队列等待重试

            except asyncio.CancelledError:
                if self._stuck_detector_task:
                    self._stuck_detector_task.cancel()
                raise
            except Exception as e:
                logger.error(f"Worker stream read error: {e}", exc_info=True)
                await asyncio.sleep(1)

    def prepare_executor(
        self,
        executor_code: str,
        app_config: dict | None = None,
        user_tool_classes: list | None = None,
        workspace_id: str = "",
        run_id: str = "",
    ) -> ExecutorProtocol:
        """Build an Executor with startup-initialized tool services.

        The ToolCaller singleton and default ToolProvider are created once
        in ``__init__`` and reused across runs.  When ``user_tool_classes``
        is provided (user-defined ExternalTools loaded from the DB), a
        per-run ToolProvider and ToolCaller are created that include them.

        Args:
            executor_code: Executor identifier (matches TEMPLATE["executor_code"]).
            app_config: App-level config that overrides the template defaults.
            user_tool_classes: Optional list of dynamic ExternalTool subclasses
                loaded from the database for the current user.

        Returns:
            A fully configured Executor instance.

        Raises:
            ValueError: When executor_code is not registered.
        """
        executor_cls = ExecutorRegistry._get_singleton_instance().get(executor_code)
        if not executor_cls:
            raise ValueError(f"Executor with code '{executor_code}' not found")

        # Build config from template defaults
        config: dict = {}
        template_config = executor_cls.TEMPLATE.get("config")
        if template_config is not None:
            if hasattr(template_config, "model_dump"):
                config = template_config.model_dump()
            elif isinstance(template_config, dict):
                config = dict(template_config)

        # App-level config overrides template defaults
        if app_config:
            config.update(app_config)

        # ── Inject run context ────────────────────────────────────
        config["workspace_id"] = workspace_id
        config["run_id"] = run_id

        # ── Inject tool services ─────────────────────────────────
        if user_tool_classes:
            # Per-run ToolCaller that knows about user-defined tools
            user_instances = {cls.METADATA.name: cls() for cls in user_tool_classes}
            config["tool_caller"] = RegistryToolCaller(
                extra_instances=user_instances,
            )

            # Per-run ToolProvider that includes user tools
            base_extra = list(self._default_tool_provider._extra_tool_classes)
            if not config.get("enable_browser_tools", True):
                base_extra = []
            config["tool_provider"] = RegistryToolProvider(
                extra_tool_classes=base_extra + list(user_tool_classes),
            )
        else:
            # No user tools — reuse startup singletons
            config["tool_caller"] = self._tool_caller
            config["tool_provider"] = RegistryToolProvider()

        return executor_cls(config)

    async def handle_event(self, event: Event):
        """
        处理来自 run_tasks stream 的消息。

        Args:
            event: Parsed event data from Redis stream
        """
        # Expire cached ORM objects so each run starts with fresh DB state.
        # This prevents stale identity-map entries from accumulating across
        # runs without requiring a full per-run session swap.
        self.db.expire_all()

        run_id = None
        try:
            # 1. Validate event data
            if not event.run_id:
                logger.warning("Received message without run_id, skipping")
                return

            if not event.executor_code:
                logger.error(
                    "Received message without executor_code, cannot determine executor"
                )
                return

            run_id = event.run_id if isinstance(event.run_id, UUID) else UUID(str(event.run_id))
            input_data = UserMessage(**(event.payload or {}))

            # 2. 获取 Run 及相关数据
            run, app_config = await self._fetch_run_data(run_id)
            if not run:
                raise ValueError(f"Run {run_id} not found")

            if RunStateMachine.is_terminal(run.status):
                logger.warning(f"Run {run_id} is terminal, skipping execution")
                return

            # 2.5 Evaluate workspace triggers and enrich payload
            workspace_id_str = str(run.workspace_id)
            try:
                from aiwen.services.triggers.trigger_processor import (
                    process_event_triggers,
                )
                trigger_results = await process_event_triggers(self.db, workspace_id_str, event)
                if trigger_results:
                    event.payload = {**(event.payload or {}), "_trigger_context": trigger_results}
                    input_data = UserMessage(**(event.payload or {}))
            except Exception as _trigger_err:
                logger.error(f"Trigger processing failed for run {run_id}: {_trigger_err}", exc_info=True)

            # 3. Load user-defined external tools (incl. chain/pipeline tools)
            user_tool_classes = []
            if run.user_id:
                user_tool_classes = await DynamicToolLoader.load_user_tools(
                    self.db,
                    run.user_id,
                    run.workspace_id,
                )

            # 4. Prepare Executor with user tools injected
            workspace_id = str(run.workspace_id)
            executor = self.prepare_executor(
                event.executor_code,
                app_config,
                user_tool_classes or None,
                workspace_id=workspace_id,
                run_id=str(run_id),
            )
            self.runtime.attach(run_id, executor)
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
        executor: ExecutorProtocol,
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
            resume_data = await self._prepare_resume_data(
                run_id, run.waiting_for, user_message
            )
            await self.state_machine.resume_from_tool(run_id, auto_commit=True)
            await self._execute_run(executor, resume_data, run_id, workspace_id)

        elif run_status == RunStatus.RUNNING.value:
            await self._execute_run(executor, user_message, run_id, workspace_id)

    async def _prepare_resume_data(
        self, run_id: UUID, waiting_for: dict | None, user_message: dict | UserMessage
    ) -> dict:
        """Prepare resume data by fetching approval info from Redis.

        Merges the original user_message with the stored waiting_for
        snapshot and the user's approval decision so the executor can
        reconstruct the conversation and continue from the pause point.

        Args:
            run_id: Run UUID
            waiting_for: The ``Run.waiting_for`` dict stored at pause time.
            user_message: Original input data (dict or UserMessage).

        Returns:
            Dict with ``_resumed``, ``_waiting_info``, ``_approval`` keys
            that the executor's ``stream()`` checks on entry.
        """
        # Fetch the short-lived approval decision from Redis
        resume_key = f"{REDIS_RUN_LABEL}:{run_id}:{REDIS_RUN_RESUME_APPROVAL_SUFFIX}"
        raw_approval = await self.redis.get(resume_key)
        approval_data = json.loads(raw_approval) if raw_approval else {}

        if raw_approval:
            await self.redis.delete(resume_key)

        # Normalize user_message to a plain dict
        if hasattr(user_message, "model_dump") and callable(
            user_message.model_dump
        ):
            base = user_message.model_dump()
        elif isinstance(user_message, dict):
            base = dict(user_message)
        else:
            base = {"message": str(user_message)}

        return {
            **base,
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
        executor: ExecutorProtocol,
        user_message: UserMessage | dict,
        run_id: UUID,
        workspace_id: str,
    ):
        """
        执行 Agent 并通过 EventPublisher 发布所有事件。
        """
        event_count = 0
        check_interval = 10  # 每处理 10 个事件检查一次取消状态
        heartbeat_interval = 30  # seconds between heartbeat events
        last_heartbeat = datetime.now(UTC)

        try:
            async for event in executor.stream(user_message):
                # 通过 EventPublisher 发布事件（统一格式）
                await self._publish_event(event, run_id, workspace_id)

                # Publish heartbeat if enough time has elapsed
                now = datetime.now(UTC)
                if (now - last_heartbeat).total_seconds() >= heartbeat_interval:
                    await self.event_publisher.publish(
                        event_type=EventType.AGENT_HEARTBEAT,
                        workspace_id=workspace_id,
                        run_id=str(run_id),
                        payload={"timestamp": now.isoformat()},
                        auto_commit=True,
                    )
                    last_heartbeat = now

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

    async def _publish_event(
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
