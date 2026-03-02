# aiwen/workers/executor_worker.py
"""
Event Worker - 事件驱动的 Agent 执行器

监听 run_tasks stream，执行 Agent 并通过 EventPublisher 发布所有事件。
所有事件使用统一的 EventPublisher 格式，前端只需处理一种结构。

并发模型
--------
每条 Redis 消息在独立的 asyncio Task 中处理，每个 Task 拥有独立的
DB session / EventPublisher / RunStateMachine。这样多个 run 的 LLM 调用
可以真正并发，互不阻塞。

共享状态（Worker 级别，asyncio 单线程安全）：
  - runtime           : ExecutorInstanceManager — 跨 run 的 executor 实例
  - _executor_locks   : 每个 run_id 一把锁，防止同一 run 的事件交错处理
  - _tool_caller      : RegistryToolCaller 单例
  - _default_tool_provider : RegistryToolProvider 单例
  - _base_config_cache: executor template 配置缓存
"""

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from uuid import UUID

import redis.asyncio as redis_async
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.config.factory import get_settings
from aiwen.core.enums import EventType
from aiwen.core.interfaces.protocols import ExecutorProtocol
from aiwen.extensions.database import get_session
from aiwen.models.app import App
from aiwen.models.events.event import Event
from aiwen.models.runs.run import Run
from aiwen.models.workspaces.workspace import Workspace
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
from aiwen.services.triggers.trigger_processor import process_event_triggers
from aiwen.services.executor.runtime import ExecutorInstanceManager
from aiwen.services.runs.run_state_machine import RunStateMachine, RunStatus
from aiwen.services.runs.stuck_run_detector import StuckRunDetector
from aiwen.utils.workspace_context_cache import set_shared_redis_client

logger = logging.getLogger(__name__)

_redis_cfg = get_settings().redis
REDIS_CONSUMER_GROUP = _redis_cfg.consumer_group
REDIS_EXECUTOR_LABEL = _redis_cfg.executor_label
REDIS_RUN_LABEL = _redis_cfg.run_label
REDIS_RUN_RESUME_APPROVAL_SUFFIX = _redis_cfg.run_resume_approval_suffix

# Event types that should be checked against workspace triggers before processing.
# Only USER_MESSAGE is included because it is the only event type whose downstream
# handler (_on_user_message) actually reads _trigger_context from the payload.
# TOOL_CALL goes to handle_tool_call (never reads _trigger_context) and
# TOOL_RESULT goes to _on_tool_result (also never reads it), so running triggers
# for those types would be wasted DB queries and tool executions.
_TRIGGER_EVENT_TYPES: frozenset[str] = frozenset({
    EventType.USER_MESSAGE,
})


@dataclass(slots=True)
class _Ctx:
    """Per-event execution context: isolated DB session + stateless services.

    Created fresh for every dispatched event so that concurrent tasks never
    share a SQLAlchemy session (sessions are not concurrency-safe).
    """

    db: AsyncSession
    publisher: EventPublisher
    state_machine: RunStateMachine


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
        runtime: ExecutorInstanceManager | None = None,
    ):
        self.redis = redis_client
        # db is kept only for the stuck-run background task which runs every
        # 120 s.  All event processing uses per-task sessions (see _dispatch).
        self._maintenance_db = db
        self.consumer_name = consumer_name
        self.runtime = runtime if runtime is not None else ExecutorInstanceManager()

        # Per-run locks: serialise concurrent process_event calls on the same executor.
        # Safe to share across tasks because asyncio is single-threaded.
        self._executor_locks: dict[UUID, asyncio.Lock] = {}

        self._stuck_detector_task: asyncio.Task | None = None

        # ── Startup-initialized tool services (stateless / reusable) ──
        self._tool_caller = RegistryToolCaller()
        self._default_tool_provider = RegistryToolProvider()
        # Cache for deserialized template base configs, keyed by executor_code.
        self._base_config_cache: dict[str, dict] = {}

        # ── Run-level event buffers ──
        # During a run, events are held in memory and only broadcast to Redis
        # for real-time SSE.  The full history is written to PostgreSQL in one
        # atomic commit when the run completes or fails.
        # Keyed by run_id string.
        self._run_event_buffers: dict[str, list[Event]] = {}
        # Tracks the current max sequence for each buffered run so that
        # subsequent per-dispatch publishers don't re-query a stale DB value.
        self._run_seq_cursors: dict[str, int] = {}

        # ── Tool prompt cache ──
        # Keyed by executor_code (default tools only, no user tools).
        # Avoids re-generating Pydantic JSON schemas + markdown for 150+
        # tools on every run.
        self._tools_info_cache: dict[str, str] = {}

        # Register this worker's Redis client so workspace_context_cache can
        # reuse it for dirty-flag checks instead of opening a new connection
        # per call.
        set_shared_redis_client(redis_client)

    @staticmethod
    def _parse_redis_event(event_data: dict[bytes, bytes]) -> Event:
        """Parse Redis stream event data into a transient Event instance."""
        return Event.from_redis_fields(event_data)

    # ── Run-level event buffer helpers ──────────────────────────────────────

    def _get_run_buffer(self, run_id: str) -> list[Event] | None:
        """Return the active in-memory buffer for a run, or None if not buffering."""
        return self._run_event_buffers.get(run_id)

    def _start_run_buffer(self, run_id: str, initial_seq: int) -> None:
        """Begin buffering events for *run_id* in memory.

        Args:
            run_id: String run ID.
            initial_seq: The sequence number that was last committed to DB for
                this run (obtained from the current publisher's counter after
                ``state_machine.start()``).  Subsequent publishers look this up
                via ``_run_seq_cursors`` to avoid re-querying a stale DB.
        """
        self._run_event_buffers[run_id] = []
        self._run_seq_cursors[run_id] = initial_seq
        logger.debug(f"Started event buffer for run {run_id} (initial_seq={initial_seq})")

    def _pop_run_buffer(self, run_id: str) -> list[Event]:
        """Remove and return all buffered events for *run_id*; cleans up cursor."""
        self._run_seq_cursors.pop(run_id, None)
        return self._run_event_buffers.pop(run_id, [])

    async def _run_triggers(self, event: Event, ctx: _Ctx) -> None:
        """Check workspace triggers and embed formatted results into the event payload.

        Queries enabled triggers for the event's workspace that match
        ``event.event_type``.  Each matched trigger's tool is executed and the
        aggregated results are formatted into a ready-to-use context string stored
        in ``event.payload["_context"]``.  All trigger-specific logic is completed
        here; the downstream executor treats ``_context`` as generic extra context
        with no knowledge of triggers.

        Only called for USER_MESSAGE events (the only type whose handler uses _context).
        Failures are logged and silently swallowed to avoid interrupting the
        main event processing pipeline.
        """
        if not event.workspace_id:
            return

        workspace_id = str(event.workspace_id)
        try:
            run_id_str = str(event.run_id) if event.run_id else None
            results = await process_event_triggers(
                ctx.db, workspace_id, event,
                publisher=ctx.publisher,
                run_id=run_id_str,
            )
            if results:
                lines = ["[Workspace context retrieved by triggers]"]
                for item in results:
                    name = item.get("trigger_name", "trigger")
                    action = item.get("tool_name", "")
                    result = item.get("result")
                    lines.append(f"\n### {name} ({action})")
                    if result is None:
                        lines.append("(no result)")
                    elif isinstance(result, (dict, list)):
                        lines.append(json.dumps(result, ensure_ascii=False, indent=2))
                    else:
                        lines.append(str(result))

                payload = dict(event.payload or {})
                payload["_context"] = "\n".join(lines)
                event.payload = payload
                logger.info(
                    "Triggers fired: workspace=%s event=%s run=%s triggers=%s",
                    workspace_id,
                    event.event_type,
                    event.run_id,
                    [r["trigger_name"] for r in results],
                )
        except Exception as e:
            logger.error(
                "Trigger processing failed for %s run=%s: %s",
                event.event_type,
                event.run_id,
                e,
                exc_info=True,
            )

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
                # Create a fresh session for each detection cycle to avoid
                # using a stale long-lived session.
                async with get_session("aiwen") as db:
                    publisher = EventPublisher(db, self.redis)
                    state_machine = RunStateMachine(db, self.redis, publisher)
                    detector = StuckRunDetector(state_machine)
                    recovered = await detector.detect_and_recover(db)
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

        self._stuck_detector_task = asyncio.create_task(self._stuck_run_detector_loop())

        while True:
            try:
                redis_messages = await self.redis.xreadgroup(
                    groupname=REDIS_CONSUMER_GROUP,
                    consumername=self.consumer_name,
                    streams={stream_name: ">"},
                    count=10,
                    block=1000,
                )

                if not redis_messages:
                    continue

                _, event_queue = redis_messages[0]
                for event_id, event_data in event_queue:
                    # Each event gets its own task + DB session so that long-running
                    # LLM calls for one run don't block event processing for others.
                    asyncio.create_task(
                        self._dispatch(stream_name, event_id, event_data),
                        name=f"event-{self.consumer_name}-{event_id}",
                    )

            except asyncio.CancelledError:
                if self._stuck_detector_task:
                    self._stuck_detector_task.cancel()
                raise
            except Exception as e:
                logger.error(f"Worker stream read error: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _dispatch(
        self, stream_name: str, event_id: bytes, event_data: dict
    ) -> None:
        """Process one Redis stream message in an isolated DB session.

        Creates a fresh session / EventPublisher / RunStateMachine so this
        task never shares mutable state with sibling tasks.  xack is sent
        only on success; failed messages re-enter the pending queue.
        """
        try:
            async with get_session("aiwen") as db:
                publisher = EventPublisher(
                    db,
                    self.redis,
                    run_buffer_provider=self._get_run_buffer,
                    run_seq_cursor=self._run_seq_cursors,
                )
                state_machine = RunStateMachine(db, self.redis, publisher)
                ctx = _Ctx(db=db, publisher=publisher, state_machine=state_machine)

                parsed_event = self._parse_redis_event(event_data)
                await self.handle_event(parsed_event, ctx)

            await self.redis.xack(stream_name, REDIS_CONSUMER_GROUP, event_id)
        except Exception as e:
            logger.error(
                f"Failed to process message {event_id}: {e}", exc_info=True
            )
            # Not acked → enters pending queue for retry

    def prepare_executor(
        self,
        executor_code: str,
        app_config: dict | None = None,
        user_tool_classes: list | None = None,
        workspace_id: str = "",
        run_id: str = "",
    ) -> ExecutorProtocol:
        """Build an Executor with startup-initialized tool services."""
        executor_cls = ExecutorRegistry._get_singleton_instance().get(executor_code)
        if not executor_cls:
            raise ValueError(f"Executor with code '{executor_code}' not found")

        # Build config from cached template defaults.
        if executor_code not in self._base_config_cache:
            template_config = executor_cls.TEMPLATE.get("config")
            base: dict = {}
            if template_config is not None:
                if hasattr(template_config, "model_dump"):
                    base = template_config.model_dump()
                elif isinstance(template_config, dict):
                    base = dict(template_config)
            self._base_config_cache[executor_code] = base

        config: dict = dict(self._base_config_cache[executor_code])

        if app_config:
            config.update(app_config)

        config["workspace_id"] = workspace_id
        config["run_id"] = run_id

        if user_tool_classes:
            user_instances = {cls.METADATA.name: cls() for cls in user_tool_classes}
            config["tool_caller"] = RegistryToolCaller(extra_instances=user_instances)

            base_extra = list(self._default_tool_provider._extra_tool_classes)
            if not config.get("enable_browser_tools", True):
                base_extra = []
            config["tool_provider"] = RegistryToolProvider(
                extra_tool_classes=base_extra + list(user_tool_classes),
            )
        else:
            config["tool_caller"] = self._tool_caller
            config["tool_provider"] = self._default_tool_provider
            # Pass cached tools_info to skip re-generating schemas on every run.
            if executor_code in self._tools_info_cache:
                config["tools_info"] = self._tools_info_cache[executor_code]

        executor = executor_cls(config)

        # Populate cache from first executor build (default tools only).
        if not user_tool_classes and executor_code not in self._tools_info_cache:
            tools_info = getattr(executor, "tools_info", None)
            if tools_info:
                self._tools_info_cache[executor_code] = tools_info
                logger.debug(f"Cached tools_info for executor '{executor_code}'")

        return executor

    async def _get_or_create_executor(
        self, event: Event, ctx: _Ctx
    ) -> ExecutorProtocol | None:
        """Get existing executor or create new one for the event's run."""
        run_id = event.run_id
        if not run_id:
            logger.debug(f"Event {event.event_type} has no run_id, skipping")
            return None

        run_id = run_id if isinstance(run_id, UUID) else UUID(str(run_id))

        existing_executor = self.runtime.get(run_id)
        if existing_executor:
            return existing_executor

        if event.event_type == EventType.USER_MESSAGE:
            return await self._create_executor_for_run(event, run_id, ctx)

        logger.warning(
            f"No executor found for run {run_id}, event {event.event_type} cannot be processed"
        )
        return None

    async def _create_executor_for_run(
        self, event: Event, run_id: UUID, ctx: _Ctx
    ) -> ExecutorProtocol | None:
        """Create and initialize a new executor for a run."""
        try:
            run, app_config = await self._fetch_run_data(run_id, ctx.db)
            if not run:
                logger.error(f"Run {run_id} not found")
                return None

            if RunStateMachine.is_terminal(run.status):
                logger.warning(f"Run {run_id} is terminal, skipping")
                return None

            # Resolve executor_code: event → workspace fallback → default
            executor_code = event.executor_code
            if not executor_code:
                executor_code = getattr(run, "_ws_executor_code", None) or "SimpleAgent"
                logger.debug(
                    "event.executor_code missing for run %s; using '%s'",
                    run_id,
                    executor_code,
                )

            user_tool_classes = []
            if run.user_id:
                try:
                    user_tool_classes = await DynamicToolLoader.load_user_tools(
                        ctx.db,
                        run.user_id,
                        run.workspace_id,
                    )
                except Exception as tool_err:
                    logger.error(f"Failed to load user tools: {tool_err}", exc_info=True)
                    await ctx.db.rollback()

            workspace_id = str(run.workspace_id)

            # Pre-warm the workspace context cache so _load_history() inside
            # the executor hits the cache instead of opening a separate DB
            # session and doing a cold DB load on the first run.
            try:
                from aiwen.utils.workspace_context_cache import get_cached_workspace_context
                await get_cached_workspace_context(ctx.db, workspace_id)
            except Exception as ctx_err:
                logger.debug(f"Workspace context pre-warm failed (non-critical): {ctx_err}")

            executor = self.prepare_executor(
                executor_code,
                app_config,
                user_tool_classes or None,
                workspace_id=workspace_id,
                run_id=str(run_id),
            )

            self.runtime.attach(run_id, executor)
            await ctx.state_machine.start(run_id, auto_commit=True)

            # Start buffering *after* the RUN_STATE_CHANGE event has been
            # committed to DB by state_machine.start().  All subsequent events
            # (agent tokens, tool calls, etc.) will be held in memory and only
            # broadcast to Redis until the run completes or fails.
            run_id_str = str(run_id)
            initial_seq = ctx.publisher._seq_counters.get(run_id_str, 0)
            self._start_run_buffer(run_id_str, initial_seq)

            return executor

        except Exception as e:
            logger.error(f"Failed to create executor for run {run_id}: {e}", exc_info=True)
            with contextlib.suppress(Exception):
                run_id_str = str(run_id)
                buffered = self._pop_run_buffer(run_id_str)
                if buffered:
                    ctx.db.add_all(buffered)
                await ctx.state_machine.fail(run_id, error=str(e), auto_commit=True)
            return None

    async def handle_event(self, event: Event, ctx: _Ctx) -> None:
        """Route events to appropriate handlers."""
        ctx.db.expire_all()

        try:
            event_type = event.event_type

            # Run workspace triggers before routing so the downstream handler
            # receives an event enriched with trigger results in _trigger_context.
            if event_type in _TRIGGER_EVENT_TYPES:
                await self._run_triggers(event, ctx)

            # Trigger-sourced TOOL_CALL / TOOL_RESULT / TOOL_ERROR events carry
            # "_source": "trigger" to signal they were already executed by the
            # trigger processor.  Skip normal routing so the tool is not
            # re-executed and the executor loop is not re-entered.
            if (event.payload or {}).get("_source") == "trigger" and event_type in (
                EventType.TOOL_CALL,
                EventType.TOOL_RESULT,
                EventType.TOOL_ERROR,
            ):
                return

            match event_type:
                case (
                    EventType.AGENT_TOKEN
                    | EventType.AGENT_MESSAGE
                    | EventType.AGENT_THINKING
                    | EventType.AGENT_PLAN_STEP
                    | EventType.AGENT_HEARTBEAT
                ):
                    return

                case (
                    EventType.RUN_CREATED
                    | EventType.RUN_STATE_CHANGE
                    | EventType.RUN_COMPLETED
                    | EventType.RUN_FAILED
                ):
                    return

                case (
                    EventType.USER_MESSAGE
                    | EventType.USER_FEEDBACK
                    | EventType.TOOL_RESULT
                    | EventType.TOOL_ERROR
                    | EventType.TOOL_PENDING
                    | EventType.TOOL_CLIENT_REQUEST
                ):
                    await self._forward_to_executor(event, ctx)

                case EventType.TOOL_CALL:
                    await handle_tool_call(
                        event, ctx.db, ctx.publisher, ctx.state_machine,
                        tool_caller=self._tool_caller,
                    )

                case EventType.RUN_CANCELLED:
                    await handle_run_cancellation(event, self.runtime)

                case (
                    EventType.TASK_CREATE
                    | EventType.TASK_UPDATE
                    | EventType.TASK_DELETE
                    | EventType.TASK_COMPLETE
                    | EventType.TASK_ASSIGN
                ):
                    await handle_task_event(event)

                case (
                    EventType.ARTIFACT_CREATE
                    | EventType.ARTIFACT_UPDATE
                    | EventType.ARTIFACT_DELETE
                    | EventType.ARTIFACT_VERSION
                ):
                    await handle_artifact_event(event)

                case (
                    EventType.WORKSPACE_CREATED
                    | EventType.WORKSPACE_UPDATED
                    | EventType.WORKSPACE_MEMBER_JOIN
                    | EventType.WORKSPACE_MEMBER_LEAVE
                    | EventType.WORKSPACE_MEMBER_ROLE_CHANGE
                    | EventType.USING_CONTEXT
                ):
                    pass

                case _:
                    logger.warning(f"Unknown event type {event_type}, skipping")

        except Exception as e:
            logger.error(f"handle_event error for {event.event_type}: {e}", exc_info=True)
            if event.run_id:
                try:
                    run_id = (
                        event.run_id
                        if isinstance(event.run_id, UUID)
                        else UUID(str(event.run_id))
                    )
                    run_id_str = str(run_id)
                    # Flush buffered events so the run history is not lost on failure.
                    buffered = self._pop_run_buffer(run_id_str)
                    if buffered:
                        ctx.db.add_all(buffered)
                    await ctx.state_machine.fail(run_id, error=str(e), auto_commit=True)
                    self.runtime.release(run_id)
                except Exception as fail_err:
                    logger.error(f"Failed to mark run as failed: {fail_err}")

    async def _forward_to_executor(self, event: Event, ctx: _Ctx) -> None:
        """Forward event to executor for processing and publish emitted events."""
        executor = await self._get_or_create_executor(event, ctx)
        if not executor:
            return

        run_id = (
            event.run_id if isinstance(event.run_id, UUID) else UUID(str(event.run_id))
        )
        run_id_str = str(run_id)
        workspace_id = str(event.workspace_id)

        if run_id not in self._executor_locks:
            self._executor_locks[run_id] = asyncio.Lock()

        async with self._executor_locks[run_id]:
            async for output_event in executor.process_event(event):
                await self._publish_event(output_event, run_id, workspace_id, ctx)

            # Events are buffered in memory during execution; no per-iteration
            # commit needed.  Flush everything atomically when the run ends.

            if not getattr(executor, "_pending_tool_ids", None):
                # Drain the in-memory buffer and persist all events together with
                # the final run state change in a single DB commit.
                buffered = self._pop_run_buffer(run_id_str)
                if buffered:
                    ctx.db.add_all(buffered)

                try:
                    await ctx.state_machine.complete(run_id, auto_commit=True)
                except Exception as complete_err:
                    logger.error(f"Failed to complete run {run_id}: {complete_err}")

                self._executor_locks.pop(run_id, None)
                self.runtime.release(run_id)
                ctx.publisher.release_sequence_counter(run_id_str)
                logger.debug(f"Run {run_id} finished and executor released")

    async def _fetch_run_data(
        self, run_id: UUID, db: AsyncSession
    ) -> tuple[Run | None, dict | None]:
        """Fetch Run and resolve executor config via 3-level fallback.

        Fallback order:
          1. run.app_id → App.config  (legacy)
          2. workspace.executor_config  (new native config)
          3. None → executor template defaults applied in prepare_executor()
        """
        stmt = (
            select(Run, App.config, Workspace.executor_config, Workspace.executor_code)
            .outerjoin(App, Run.app_id == App.id)
            .outerjoin(Workspace, Run.workspace_id == Workspace.id)
            .where(Run.id == str(run_id))
        )
        result = await db.execute(stmt)
        row = result.first()
        if not row:
            return None, None
        run, app_config, ws_executor_config, ws_executor_code = row

        # 3-level fallback for executor config
        resolved_config = app_config or ws_executor_config or None

        # If the event had no executor_code, store workspace's on the run context
        # so _create_executor_for_run can use it (passed via event.executor_code already).
        # We attach ws_executor_code to the run object transiently for caller access.
        if ws_executor_code and not run.app_id:
            run._ws_executor_code = ws_executor_code  # type: ignore[attr-defined]

        return run, resolved_config

    async def _publish_event(
        self,
        event: Event,
        run_id: UUID,
        workspace_id: str,
        ctx: _Ctx,
    ) -> None:
        """Publish an executor-emitted Event via this task's EventPublisher.

        Uses auto_commit=False — all events in one executor iteration are
        flushed immediately (for Redis broadcast) and committed in a single
        batch by _forward_to_executor after the iteration completes.
        """
        try:
            await ctx.publisher.publish(
                event_type=str(event.event_type),
                workspace_id=workspace_id,
                run_id=str(run_id),
                payload=event.payload,
                auto_commit=False,
            )
        except Exception as e:
            logger.error(f"Error publishing agent event for run {run_id}: {e}")
