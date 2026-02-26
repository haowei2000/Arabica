# aiwen/workers/executor_worker.py
"""
Event Worker - 事件驱动的 Agent 执行器

监听 run_tasks stream，执行 Agent 并通过 EventPublisher 发布所有事件。
所有事件使用统一的 EventPublisher 格式，前端只需处理一种结构。
"""

import asyncio
import logging
from uuid import UUID

import redis.asyncio as redis_async
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.config.factory import get_settings
from aiwen.core.enums import EventType
from aiwen.core.interfaces.protocols import ExecutorProtocol
from aiwen.models.app import App
from aiwen.models.events.event import Event
from aiwen.models.runs.run import Run
from aiwen.registries.core import ExecutorRegistry
from aiwen.registries.dynamic_loader import DynamicToolLoader
from aiwen.registries.tool_service import RegistryToolCaller, RegistryToolProvider
from aiwen.services.events.event_publisher import EventPublisher
from aiwen.services.events.handlers import (
    handle_artifact_event,
    handle_run_cancellation,
    handle_task_event,
    handle_tool_call,
)
from aiwen.services.executor.runtime import ExecutorInstanceManager
from aiwen.services.runs.run_state_machine import RunStateMachine, RunStatus
from aiwen.services.runs.stuck_run_detector import StuckRunDetector
import contextlib

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
        # Fallback: Map legacy DEFAULT001 to SimpleAgent
        if executor_code == "DEFAULT001":
            logger.warning("Mapping legacy executor code 'DEFAULT001' to 'SimpleAgent'")
            executor_code = "SimpleAgent"

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

    async def _get_or_create_executor(self, event: Event) -> ExecutorProtocol | None:
        """Get existing executor or create new one for the event's run.

        Args:
            event: The event to process

        Returns:
            Executor instance or None if not applicable
        """
        run_id = event.run_id
        if not run_id:
            logger.debug(
                f"Event {event.event_type} has no run_id, skipping executor lookup"
            )
            return None

        run_id = run_id if isinstance(run_id, UUID) else UUID(str(run_id))

        # Check if executor already exists for this run
        existing_executor = self.runtime.get(run_id)
        if existing_executor:
            return existing_executor

        # For user.message events, create a new executor
        if event.event_type == EventType.USER_MESSAGE:
            return await self._create_executor_for_run(event, run_id)

        # For other events, no executor means we can't process
        logger.warning(
            f"No executor found for run {run_id}, event {event.event_type} cannot be processed"
        )
        return None

    async def _create_executor_for_run(
        self, event: Event, run_id: UUID
    ) -> ExecutorProtocol | None:
        """Create and initialize a new executor for a run.

        Args:
            event: The user.message event
            run_id: The run ID

        Returns:
            Initialized executor instance or None on failure
        """
        try:
            if not event.executor_code:
                logger.error("Event has no executor_code, cannot create executor")
                return None

            # Fetch run data from database
            run, app_config = await self._fetch_run_data(run_id)
            if not run:
                logger.error(f"Run {run_id} not found")
                return None

            if RunStateMachine.is_terminal(run.status):
                logger.warning(f"Run {run_id} is terminal, skipping")
                return None

            # Load user-defined tools
            user_tool_classes = []
            if run.user_id:
                try:
                    user_tool_classes = await DynamicToolLoader.load_user_tools(
                        self.db,
                        run.user_id,
                        run.workspace_id,
                    )
                except Exception as tool_err:
                    logger.error(
                        f"Failed to load user tools: {tool_err}", exc_info=True
                    )
                    await self.db.rollback()

            # Create executor
            workspace_id = str(run.workspace_id)
            executor = self.prepare_executor(
                event.executor_code,
                app_config,
                user_tool_classes or None,
                workspace_id=workspace_id,
                run_id=str(run_id),
            )

            # Attach to runtime and transition to running
            self.runtime.attach(run_id, executor)
            await self.state_machine.start(run_id, auto_commit=True)

            return executor

        except Exception as e:
            logger.error(
                f"Failed to create executor for run {run_id}: {e}", exc_info=True
            )
            with contextlib.suppress(Exception):
                await self.state_machine.fail(run_id, error=str(e), auto_commit=True)
            return None

    async def handle_event(self, event: Event):
        """
        Route events to appropriate handlers using match-case.

        Event routing strategy:
        - Output events (agent.*, tool.result/error, run.*): Skip
        - User events (user.message, user.feedback): Forward to executor
        - Tool events: Some to executor, some to dedicated handlers
        - Run/Task/Artifact events: To dedicated handlers
        - Other events: Forward to executor

        Args:
            event: Parsed event data from Redis stream
        """
        # Expire cached ORM objects so each run starts with fresh DB state
        self.db.expire_all()

        try:
            event_type = event.event_type

            # Route event based on type using match-case
            match event_type:
                # ── Skip: Output events published by executor or state machine ──
                case (
                    EventType.AGENT_TOKEN
                    | EventType.AGENT_MESSAGE
                    | EventType.AGENT_THINKING
                    | EventType.AGENT_PLAN_STEP
                    | EventType.AGENT_HEARTBEAT
                ):
                    logger.debug(f"Skipping agent output event: {event_type}")
                    return

                case (
                    EventType.RUN_CREATED
                    | EventType.RUN_STATE_CHANGE
                    | EventType.RUN_COMPLETED
                    | EventType.RUN_FAILED
                ):
                    logger.debug(f"Skipping run lifecycle event: {event_type}")
                    return

                # ── Forward to Executor: User and core agent events ──
                case (
                    EventType.USER_MESSAGE
                    | EventType.USER_FEEDBACK
                    | EventType.TOOL_RESULT
                    | EventType.TOOL_ERROR
                    | EventType.TOOL_PENDING
                    | EventType.TOOL_CLIENT_REQUEST
                ):
                    await self._forward_to_executor(event)

                # ── Dedicated Handlers: Tool execution ──
                case EventType.TOOL_CALL:
                    await handle_tool_call(
                        event, self.db, self.event_publisher, self.state_machine
                    )

                # ── Dedicated Handlers: Run lifecycle ──
                case EventType.RUN_CANCELLED:
                    await handle_run_cancellation(event, self.runtime)

                # ── Dedicated Handlers: Task lifecycle ──
                case (
                    EventType.TASK_CREATE
                    | EventType.TASK_UPDATE
                    | EventType.TASK_DELETE
                    | EventType.TASK_COMPLETE
                    | EventType.TASK_ASSIGN
                ):
                    await handle_task_event(event)

                # ── Dedicated Handlers: Artifact lifecycle ──
                case (
                    EventType.ARTIFACT_CREATE
                    | EventType.ARTIFACT_UPDATE
                    | EventType.ARTIFACT_DELETE
                    | EventType.ARTIFACT_VERSION
                ):
                    await handle_artifact_event(event)

                # ── Dedicated Handlers: Workspace events ──
                case (
                    EventType.WORKSPACE_CREATED
                    | EventType.WORKSPACE_UPDATED
                    | EventType.WORKSPACE_MEMBER_JOIN
                    | EventType.WORKSPACE_MEMBER_LEAVE
                    | EventType.WORKSPACE_MEMBER_ROLE_CHANGE
                ):
                    logger.debug(f"Workspace event {event_type} - no handler yet")

                # ── Dedicated Handlers: Context events ──
                case EventType.USING_CONTEXT:
                    logger.debug(f"Context event {event_type} - forwarding to executor")

                # ── Default: Forward to executor ──
                case _:
                    logger.warning(
                        f"Unknown event type {event_type}, forwarding to executor"
                    )
                    await self._forward_to_executor(event)

        except Exception as e:
            logger.error(
                f"handle_event error for {event.event_type}: {e}", exc_info=True
            )
            if event.run_id:
                try:
                    run_id = (
                        event.run_id
                        if isinstance(event.run_id, UUID)
                        else UUID(str(event.run_id))
                    )
                    await self.state_machine.fail(
                        run_id, error=str(e), auto_commit=True
                    )
                except Exception as fail_err:
                    logger.error(f"Failed to mark run as failed: {fail_err}")

    async def _forward_to_executor(self, event: Event):
        """Forward event to executor for processing and publish emitted events.

        Args:
            event: Event to forward
        """
        # Get or create executor
        executor = await self._get_or_create_executor(event)
        if not executor:
            logger.debug(
                f"No executor available for event {event.event_type}, skipping"
            )
            return

        # Publish all events emitted by the executor
        run_id = (
            event.run_id if isinstance(event.run_id, UUID) else UUID(str(event.run_id))
        )
        workspace_id = str(event.workspace_id)

        async for output_event in executor.process_event(event):
            await self._publish_event(output_event, run_id, workspace_id)

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
        event: Event,
        run_id: UUID,
        workspace_id: str,
    ):
        """Publish an Event emitted by the executor through EventPublisher."""
        try:
            await self.event_publisher.publish(
                event_type=str(event.event_type),
                workspace_id=workspace_id,
                run_id=str(run_id),
                payload=event.payload,  # type: ignore[arg-type]
                auto_commit=True,
            )
        except Exception as e:
            logger.error(f"Error publishing agent event for run {run_id}: {e}")
