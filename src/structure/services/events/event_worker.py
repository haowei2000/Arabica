# structure/workers/executor_worker.py
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
from dataclasses import dataclass
import logging
from time import perf_counter
from uuid import UUID

import redis.asyncio as redis_async
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.config.factory import get_settings
from structure.core.enums import EventType
from structure.core.interfaces.protocols import ExecutorProtocol
from structure.extensions.database import get_session
from structure.models.app import App
from structure.models.events.event import Event
from structure.models.runs.run import Run
from structure.models.workspaces.workspace import Workspace
from structure.registries.core import ExecutorRegistry
from structure.registries.dynamic_loader import DynamicToolLoader
from structure.registries.tool_service import RegistryToolCaller, RegistryToolProvider
from structure.services.events.event_commands import (
    REDIS_CONSUMER_GROUP,
    EventCommand,
    executor_command_stream_name,
)
from structure.services.events.event_publisher import (
    EventPublisher,
)
from structure.services.events.handlers import (
    handle_artifact_event,
    handle_run_cancellation,
    handle_task_event,
    handle_tool_call,
)
from structure.services.events.run_event_router import (
    RunEventRouter,
    RunExecutionSnapshot,
    RunRouteAction,
)
from structure.services.executor.runtime import ExecutorInstanceManager
from structure.services.runs.run_state_machine import RunStateMachine
from structure.services.runs.stuck_run_detector import StuckRunDetector
from structure.services.triggers.trigger_processor import process_event_triggers
from structure.utils.workspace_context_cache import set_shared_redis_client

logger = logging.getLogger(__name__)

_redis_cfg = get_settings().redis
REDIS_RUN_LABEL = _redis_cfg.run_label
REDIS_RUN_RESUME_APPROVAL_SUFFIX = _redis_cfg.run_resume_approval_suffix


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
        self._dispatch_semaphore = asyncio.Semaphore(
            get_settings().redis.worker_max_concurrent_events
        )

        self._stuck_detector_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

        # ── Startup-initialized tool services (stateless / reusable) ──
        self._tool_caller = RegistryToolCaller()
        self._default_tool_provider = RegistryToolProvider()
        self._router = RunEventRouter()
        # Cache for deserialized template base configs, keyed by executor_code.
        self._base_config_cache: dict[str, dict] = {}

        # ── Run-level event buffers ──
        # Compatibility state for older handler hooks. Durable semantic events
        # now write through immediately in EventPublisher; this buffer should
        # stay empty except for legacy callers that still pass a flush callback.
        self._run_event_buffers: dict[str, list[Event]] = {}
        # Tracks the current max sequence for each buffered run so that
        # subsequent per-dispatch publishers don't re-query a stale DB value.
        self._run_seq_cursors: dict[str, int] = {}

        # ── Tool schema cache ──
        # Keyed by executor_code (default tools only, no user tools).
        # Avoids re-generating Pydantic JSON schemas for 150+ tools on every run.
        # Value is whatever the strategy's format_tools() returns:
        # list[dict] for FunctionCallingStrategy, str for PromptCallingStrategy.
        self._tools_info_cache: dict[str, object] = {}

        # ── Per-run tool callers ──
        # When a run has user-defined tools (external/mcp), a per-run
        # RegistryToolCaller is created with those tools as extra_instances.
        # TOOL_CALL events are routed outside the executor so we store the
        # caller here keyed by run_id string to pass it to handle_tool_call.
        self._run_tool_callers: dict[str, RegistryToolCaller] = {}

        # Register this worker's Redis client so workspace_context_cache can
        # reuse it for dirty-flag checks instead of opening a new connection
        # per call.
        set_shared_redis_client(redis_client)

    # ── Run-level event buffer helpers ──────────────────────────────────────

    def _get_run_buffer(self, run_id: str) -> list[Event] | None:
        """Return the active in-memory buffer for a run, or None if not buffering."""
        return self._run_event_buffers.get(run_id)

    def _pop_run_buffer(self, run_id: str) -> list[Event]:
        """Remove and return all buffered events for *run_id*; cleans up cursor."""
        self._run_seq_cursors.pop(run_id, None)
        return self._run_event_buffers.pop(run_id, [])

    async def _flush_buffer_to_db(self, run_id: str, db: AsyncSession) -> None:
        """Drain the in-memory event buffer into *db* without committing.

        Called just before ``pause_for_tool`` when the run is waiting for user
        input so that the AGENT_MESSAGE (with ask_for_user tool call) is
        persisted in the same transaction as the run state change.  The
        subsequent ``auto_commit=True`` in ``pause_for_tool`` then commits
        both the buffered events and the waiting state atomically.
        """
        buffered = self._pop_run_buffer(run_id)
        if buffered:
            db.add_all(buffered)
            logger.debug(
                f"Flushed {len(buffered)} buffered events to DB for run {run_id}"
            )

    async def _run_triggers(self, event: Event, ctx: _Ctx) -> None:
        """Execute workspace triggers for the event and publish their tool call events.

        Each matched trigger runs its configured tool and publishes
        AGENT_MESSAGE + TOOL_CALL + TOOL_RESULT/TOOL_ERROR events directly to
        the run stream.  The executor receives these as first-class conversation
        events — no separate context injection is needed.
        """
        if not event.workspace_id:
            return

        workspace_id = str(event.workspace_id)
        try:
            run_id_str = str(event.run_id) if event.run_id else None
            results = await process_event_triggers(
                ctx.db,
                workspace_id,
                event,
                publisher=ctx.publisher,
                run_id=run_id_str,
            )
            if results:
                logger.info(
                    "Triggers fired: workspace=%s run=%s triggers=%s",
                    workspace_id,
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
                async with get_session("structure") as db:
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

    async def start(self, workspace_id: str):
        """Start consuming executor commands using consumer groups."""
        stream_name = executor_command_stream_name(workspace_id)
        await self._ensure_consumer_group(stream_name)
        logger.info(
            f"Worker '{self.consumer_name}' listening on executor command stream: {stream_name}"
        )

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
                    task = asyncio.create_task(
                        self._dispatch_limited(stream_name, event_id, event_data),
                        name=f"event-{self.consumer_name}-{event_id}",
                    )
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)

            except asyncio.CancelledError:
                if self._stuck_detector_task:
                    self._stuck_detector_task.cancel()
                raise
            except Exception as e:
                logger.error(f"Worker stream read error: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _dispatch_limited(
        self, stream_name: str, event_id: bytes, event_data: dict
    ) -> None:
        """Process an event under the worker-level concurrency cap."""
        async with self._dispatch_semaphore:
            await self._dispatch(stream_name, event_id, event_data)

    async def _dispatch(
        self, stream_name: str, event_id: bytes, event_data: dict
    ) -> None:
        """Process one executor command in an isolated DB session.

        Commands contain only a durable event id. The worker reloads the event
        and current run history from PostgreSQL, so semantic history survives a
        worker crash/restart. xack is sent after successful handling.
        """
        parsed_event: Event | None = None
        try:
            command = EventCommand.from_redis_fields(event_data)
            if command.created_at_ms is not None:
                from structure.services.events.event_commands import (
                    command_created_at_ms,
                )

                logger.info(
                    "command_queue_lag_ms=%s workspace=%s run=%s event=%s",
                    command_created_at_ms() - command.created_at_ms,
                    command.workspace_id,
                    command.run_id,
                    command.event_id,
                )

            async with get_session("structure") as db:
                parsed_event = await self._load_event_for_command(db, command)
                if parsed_event is None:
                    raise ValueError(f"Command event not found: {command.event_id}")
                if command.executor_code and not parsed_event.executor_code:
                    parsed_event.executor_code = command.executor_code

                publisher = EventPublisher(
                    db,
                    self.redis,
                    run_buffer_provider=self._get_run_buffer,
                    run_seq_cursor=self._run_seq_cursors,
                )
                state_machine = RunStateMachine(db, self.redis, publisher)
                ctx = _Ctx(db=db, publisher=publisher, state_machine=state_machine)
                run_events = await self._load_run_events(db, str(parsed_event.run_id))
                await self.handle_event(parsed_event, ctx, run_events)

            await self.redis.xack(stream_name, REDIS_CONSUMER_GROUP, event_id)
        except Exception as e:
            logger.error("Failed to process message %s: %s", event_id, e, exc_info=True)
            # The handler couldn't surface the error itself (likely a poisoned
            # session or an unhandled path).  Try once more with a fresh DB
            # session so the run is marked failed and the SSE client sees
            # a terminal event instead of a silently stalled stream.
            if parsed_event is not None and parsed_event.run_id:
                try:
                    run_id = (
                        parsed_event.run_id
                        if isinstance(parsed_event.run_id, UUID)
                        else UUID(str(parsed_event.run_id))
                    )
                    await self._safe_fail_run(run_id, str(e))
                except Exception as report_err:
                    logger.error(
                        "Failed to report dispatch error for run %s: %s",
                        parsed_event.run_id,
                        report_err,
                    )
            # Ack the message regardless — leaving it in PEL only delays the
            # next message and re-runs would hit the same crash.
            with contextlib.suppress(Exception):
                await self.redis.xack(stream_name, REDIS_CONSUMER_GROUP, event_id)

    async def _safe_fail_run(self, run_id: UUID, error_msg: str) -> None:
        """Mark *run_id* failed using a fresh DB session.

        Called from outer/recovery paths where the in-flight session may be
        poisoned (rolled back or detached after an exception).  Guarantees a
        ``RUN_STATE_CHANGE`` event reaches Redis so SSE clients exit cleanly.
        """
        async with get_session("structure") as fresh_db:
            publisher = EventPublisher(fresh_db, self.redis)
            state_machine = RunStateMachine(fresh_db, self.redis, publisher)
            await state_machine.fail(run_id, error=error_msg, auto_commit=True)
        # Drop any lingering per-run state so the next attempt starts clean.
        run_id_str = str(run_id)
        self._run_tool_callers.pop(run_id_str, None)
        self._run_event_buffers.pop(run_id_str, None)
        self._run_seq_cursors.pop(run_id_str, None)
        self.runtime.release(run_id)

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

        # Always create a per-run caller — only MCP-imported tools are available.
        # Inner tools must be imported from the structure-mcp server first.
        user_instances = {cls.METADATA.name: cls() for cls in (user_tool_classes or [])}
        per_run_caller = RegistryToolCaller(extra_instances=user_instances)
        config["tool_caller"] = per_run_caller
        if run_id:
            self._run_tool_callers[run_id] = per_run_caller

        config["tool_provider"] = RegistryToolProvider(
            extra_tool_classes=list(user_tool_classes or []),
        )

        # Pass cached tools_info only when no user tools (stable schema).
        if not user_tool_classes and executor_code in self._tools_info_cache:
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
        """Get existing executor or create a new one for the event's run.

        For TO_EXECUTOR envelopes, restores executor_code from the envelope
        payload so _create_executor_for_run receives the original value.
        """
        run_id = event.run_id
        if not run_id:
            logger.debug("Event %s has no run_id, skipping", event.event_type)
            return None

        run_id = run_id if isinstance(run_id, UUID) else UUID(str(run_id))

        existing = self.runtime.get(run_id)
        if existing:
            return existing

        # Restore executor_code from TO_EXECUTOR envelope payload.
        if str(event.event_type) == EventType.TO_EXECUTOR and not event.executor_code:
            event.executor_code = (event.payload or {}).get("_original_executor_code")  # type: ignore[assignment]

        return await self._create_executor_for_run(event, run_id, ctx)

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
                    logger.error(
                        f"Failed to load user tools: {tool_err}", exc_info=True
                    )
                    await ctx.db.rollback()

            workspace_id = str(run.workspace_id)

            # Resolve ChatModel from DB: prefer workspace-pinned model, fall back to default.
            try:
                from structure.services.llm.chat_model_crud import ChatModelCRUD

                crud = ChatModelCRUD(ctx.db)
                chat_model_id = (app_config or {}).get("chat_model_id")
                chat_model = (
                    await crud.get_by_id(str(chat_model_id))
                    if chat_model_id
                    else await crud.get_default()
                )
                if chat_model:
                    if app_config is None:
                        app_config = {}
                    app_config["model_provider"] = chat_model.provider
                    app_config["model_name"] = chat_model.model_id
                    if chat_model.api_key_ref:
                        app_config["api_key"] = chat_model.api_key_ref
                    if chat_model.base_url:
                        app_config["base_url"] = chat_model.base_url
                    logger.info(
                        "_create_executor_for_run: using ChatModel '%s' (%s/%s) for run %s",
                        chat_model.name,
                        chat_model.provider,
                        chat_model.model_id,
                        run_id,
                    )
            except Exception as model_err:
                logger.warning("Failed to load ChatModel (non-critical): %s", model_err)

            # Pre-warm the workspace context cache so _load_history() inside
            # the executor hits the cache instead of opening a separate DB
            # session and doing a cold DB load on the first run.
            try:
                from structure.utils.workspace_context_cache import (
                    get_cached_workspace_context,
                )

                await get_cached_workspace_context(ctx.db, workspace_id)
            except Exception as ctx_err:
                logger.debug(
                    f"Workspace context pre-warm failed (non-critical): {ctx_err}"
                )

            executor = self.prepare_executor(
                executor_code,
                app_config,
                user_tool_classes or None,
                workspace_id=workspace_id,
                run_id=str(run_id),
            )

            self.runtime.attach(run_id, executor)
            if run.status == "pending":
                await ctx.state_machine.start(run_id, auto_commit=True)

            return executor

        except Exception as e:
            logger.error(
                f"Failed to create executor for run {run_id}: {e}", exc_info=True
            )
            with contextlib.suppress(Exception):
                run_id_str = str(run_id)
                buffered = self._pop_run_buffer(run_id_str)
                if buffered:
                    ctx.db.add_all(buffered)
                await ctx.state_machine.fail(run_id, error=str(e), auto_commit=True)
            return None

    async def handle_event(
        self, event: Event, ctx: _Ctx, run_events: list[Event]
    ) -> None:
        """Route events using an explicit durable run snapshot."""
        ctx.db.expire_all()

        try:
            event_type = str(event.event_type)
            if event_type == str(EventType.RUN_CANCELLED):
                await handle_run_cancellation(event, self.runtime)
                return
            if event_type.startswith("task."):
                await handle_task_event(event)
                return
            if event_type.startswith("artifact."):
                await handle_artifact_event(event)
                return
            if event_type.startswith("workspace."):
                return

            run_id = event.run_id
            if not run_id:
                logger.debug("Event %s has no run_id, skipping", event.event_type)
                return

            run_status = await self._load_run_status(ctx.db, str(run_id))
            snapshot = RunExecutionSnapshot.from_events(
                run_events,
                run_status=run_status,
            )
            action = self._router.route(event, snapshot)
            logger.debug(
                "handle_event: run=%s event=%s action=%s snapshot=%s",
                run_id,
                event.event_type,
                action,
                snapshot,
            )

            if action == RunRouteAction.IGNORE:
                return

            if action == RunRouteAction.EXECUTE_TOOL:
                run_tool_caller = self._run_tool_callers.get(str(run_id))
                await handle_tool_call(
                    event,
                    ctx.db,
                    ctx.publisher,
                    ctx.state_machine,
                    tool_caller=run_tool_caller or self._tool_caller,
                    flush_run_buffer=self._flush_buffer_to_db,
                )
                return

            if action == RunRouteAction.CALL_EXECUTOR:
                if event_type == str(EventType.USER_MESSAGE):
                    await self._run_triggers(event, ctx)
                    run_events = await self._load_run_events(ctx.db, str(run_id))
                await self._handle_to_executor(event, run_events, ctx)
                return

            if action == RunRouteAction.COMPLETE_RUN:
                run_uuid = run_id if isinstance(run_id, UUID) else UUID(str(run_id))
                await ctx.state_machine.complete(run_uuid, auto_commit=True)
                return

            if action == RunRouteAction.FAIL_RUN:
                run_uuid = run_id if isinstance(run_id, UUID) else UUID(str(run_id))
                await ctx.state_machine.fail(
                    run_uuid,
                    error=(event.payload or {}).get("error"),
                    auto_commit=True,
                )

        except Exception as e:
            logger.error(
                "handle_event error for %s: %s", event.event_type, e, exc_info=True
            )
            if event.run_id:
                run_id = (
                    event.run_id
                    if isinstance(event.run_id, UUID)
                    else UUID(str(event.run_id))
                )
                run_id_str = str(run_id)
                # The active session is likely poisoned by the exception that
                # bubbled up from the handler chain. Roll back first so the
                # subsequent fail() write isn't silently dropped.
                with contextlib.suppress(Exception):
                    await ctx.db.rollback()
                try:
                    buffered = self._pop_run_buffer(run_id_str)
                    if buffered:
                        ctx.db.add_all(buffered)
                    await ctx.state_machine.fail(run_id, error=str(e), auto_commit=True)
                    self._run_tool_callers.pop(run_id_str, None)
                    self.runtime.release(run_id)
                except Exception as fail_err:
                    # In-flight session truly broken — fall back to a fresh
                    # one so the failure still reaches Redis/SSE.
                    logger.error(
                        "fail() on in-flight session failed (%s); retrying with fresh session",
                        fail_err,
                    )
                    with contextlib.suppress(Exception):
                        await self._safe_fail_run(run_id, str(e))

    async def _load_event_for_command(
        self,
        db: AsyncSession,
        command: EventCommand,
    ) -> Event | None:
        """Load the durable event referenced by a worker command."""
        # A short retry shields the worker from any caller that enqueues just
        # before the DB commit becomes visible.
        for attempt in range(3):
            result = await db.execute(
                select(Event).where(Event.id == UUID(command.event_id))
            )
            event = result.scalar_one_or_none()
            if event is not None:
                return event
            if attempt < 2:
                await asyncio.sleep(0.05)
        return None

    async def _load_run_events(
        self, db: AsyncSession, run_id: str | None
    ) -> list[Event]:
        """Load durable, active events for one run from PostgreSQL."""
        if not run_id:
            return []
        result = await db.execute(
            select(Event)
            .where(
                Event.run_id == UUID(str(run_id)),
                Event.is_archived.is_(False),
            )
            .order_by(Event.sequence.asc(), Event.created_at.asc())
        )
        return list(result.scalars().all())

    async def _load_run_status(self, db: AsyncSession, run_id: str) -> str | None:
        """Load the current persisted run status for routing."""
        result = await db.execute(select(Run.status).where(Run.id == UUID(run_id)))
        return result.scalar_one_or_none()

    async def _handle_to_executor(
        self, envelope: Event, workspace_events: list[Event], ctx: _Ctx
    ) -> None:
        """Forward pre-loaded workspace events to the executor.

        Flow:
          1. Filter events if needed (though the executor now decides based on global_event).
          2. Forward events to the executor via _forward_to_executor.
          3. On completion: flush event buffer to DB, update token counts,
             finalize run state, and release all resources.
        """
        run_id = envelope.run_id
        if not run_id:
            return
        run_id_uuid = run_id if isinstance(run_id, UUID) else UUID(str(run_id))

        # Forward all events to the executor — it will filter based on global_event config.
        # We still strip TO_EXECUTOR routing events to avoid noise.
        events = [
            e for e in workspace_events if str(e.event_type) != EventType.TO_EXECUTOR
        ]
        logger.info(
            "_handle_to_executor: %d workspace events for run %s",
            len(events),
            run_id_uuid,
        )

        logger.info(
            "_handle_to_executor: forwarding to executor for run %s", run_id_uuid
        )
        run_id, run_id_str, has_pending_tools = await self._forward_to_executor(
            envelope, events, ctx
        )
        if run_id is None:
            return

        if not has_pending_tools:
            # ── Token accounting (TO_EXECUTOR level) ─────────────────────────
            executor = self.runtime.get(run_id)
            total_input = getattr(executor, "_total_input_tokens", 0) if executor else 0
            total_output = (
                getattr(executor, "_total_output_tokens", 0) if executor else 0
            )

            # ── Flush in-memory buffer ───────────────────────────────────────
            buffered = self._pop_run_buffer(run_id_str)
            logger.info(
                "_handle_to_executor: flushing %d buffered events for run %s",
                len(buffered),
                run_id,
            )
            if buffered:
                to_exec_event = next(
                    (
                        e
                        for e in reversed(buffered)
                        if str(e.event_type) == EventType.TO_EXECUTOR
                    ),
                    None,
                )
                if to_exec_event is not None:
                    to_exec_event.input_tokens = total_input
                    to_exec_event.output_tokens = total_output

                ctx.db.add_all(buffered)

            # Write token totals into Run in the same transaction.
            if total_input or total_output:
                logger.info(
                    "_handle_to_executor: updating run token counts in=%d out=%d run=%s",
                    total_input,
                    total_output,
                    run_id,
                )
                from sqlalchemy import update as sa_update

                await ctx.db.execute(
                    sa_update(Run)
                    .where(Run.id == run_id_str)
                    .values(
                        input_tokens=Run.input_tokens + total_input,
                        output_tokens=Run.output_tokens + total_output,
                    )
                )
                from structure.services.auth.quota_service import QuotaService

                await QuotaService(ctx.db).consume_run_tokens(
                    run_id_str,
                    input_tokens=total_input,
                    output_tokens=total_output,
                )

            # ── Finalize run ─────────────────────────────────────────────────
            logger.info("_handle_to_executor: completing run %s", run_id)
            try:
                await ctx.state_machine.complete(run_id, auto_commit=True)
            except Exception as complete_err:
                logger.error(f"Failed to complete run {run_id}: {complete_err}")

            self._executor_locks.pop(run_id, None)
            self._run_tool_callers.pop(run_id_str, None)
            self.runtime.release(run_id)
            ctx.publisher.release_sequence_counter(run_id_str)
            logger.info("_handle_to_executor: run %s finished and released", run_id)

    async def _forward_to_executor(
        self, envelope: Event, events: list[Event], ctx: _Ctx
    ) -> tuple[UUID | None, str, bool]:
        """Run the executor loop for the event list and publish all output events.

        Uses *envelope* (the TO_EXECUTOR event) for run_id / workspace_id /
        executor creation so the caller does not need to inspect the events list.
        Returns ``(run_id, run_id_str, has_pending_tools)``.  The caller
        (_handle_to_executor) is responsible for buffer flushing, token
        accounting, and run completion based on ``has_pending_tools``.
        """
        if not envelope.run_id:
            return None, "", False

        executor_wait_start = perf_counter()
        executor = await self._get_or_create_executor(envelope, ctx)
        if not executor:
            return None, "", False
        logger.info(
            "executor_wait_ms=%s run=%s workspace=%s",
            int((perf_counter() - executor_wait_start) * 1000),
            envelope.run_id,
            envelope.workspace_id,
        )

        run_id = (
            envelope.run_id
            if isinstance(envelope.run_id, UUID)
            else UUID(str(envelope.run_id))
        )
        run_id_str = str(run_id)
        workspace_id = str(envelope.workspace_id)

        if run_id not in self._executor_locks:
            self._executor_locks[run_id] = asyncio.Lock()

        run_done = False
        async with self._executor_locks[run_id]:
            logger.info(
                "_forward_to_executor: starting process_events for run %s (%d events)",
                run_id,
                len(events),
            )
            async for output_event in executor.process_events(events):
                if str(output_event.event_type) == EventType.RUN_COMPLETED:
                    # Internal completion signal from the executor — not published.
                    run_done = True
                    continue
                await self._publish_event(output_event, run_id, workspace_id, ctx)
            logger.info(
                "_forward_to_executor: process_events complete for run %s (done=%s)",
                run_id,
                run_done,
            )

        has_pending_tools = not run_done
        return run_id, run_id_str, has_pending_tools

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

        Durable executor events commit immediately so semantic history is
        recoverable if the worker crashes mid-run. Token events are realtime
        only and are not written to PostgreSQL.
        """
        try:
            await ctx.publisher.publish(
                event_type=str(event.event_type),
                workspace_id=workspace_id,
                run_id=str(run_id),
                payload=event.payload,
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
                auto_commit=True,
            )
        except Exception as e:
            logger.error(f"Error publishing agent event for run {run_id}: {e}")
