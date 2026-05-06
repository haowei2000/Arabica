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
from structure.services.events.event_codec import (
    RE_CODE_ARTIFACT,
    RE_CODE_FORWARD,
    RE_CODE_NOOP,
    RE_CODE_SKIP_SRC,
    RE_CODE_TASK,
    RE_CODE_WORKSPACE,
    RE_TERMINAL,
    RE_USER_MSG,
    encode,
    encode_sequence,
)
from structure.services.events.event_publisher import (
    REDIS_STREAM_EVENTS_SUFFIX,
    EventPublisher,
)
from structure.services.events.handlers import (
    handle_artifact_event,
    handle_run_cancellation,
    handle_task_event,
    handle_tool_call,
)
from structure.services.executor.runtime import ExecutorInstanceManager
from structure.services.runs.run_state_machine import RunStateMachine
from structure.services.runs.stuck_run_detector import StuckRunDetector
from structure.services.triggers.trigger_processor import process_event_triggers
from structure.utils.workspace_context_cache import set_shared_redis_client

logger = logging.getLogger(__name__)

_redis_cfg = get_settings().redis
REDIS_CONSUMER_GROUP = _redis_cfg.consumer_group
REDIS_EXECUTOR_LABEL = _redis_cfg.executor_label
REDIS_RUN_LABEL = _redis_cfg.run_label
REDIS_RUN_RESUME_APPROVAL_SUFFIX = _redis_cfg.run_resume_approval_suffix

# Event type codec (encode/decode + routing patterns) lives in event_codec.py.
# All routing decisions use the imported names directly.


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
        self._tasks: set[asyncio.Task] = set()

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
        logger.debug(
            f"Started event buffer for run {run_id} (initial_seq={initial_seq})"
        )

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
        """Start consuming messages from the workspace stream using consumer groups."""
        stream_name = f"{RE_CODE_WORKSPACE}:{workspace_id}:{REDIS_STREAM_EVENTS_SUFFIX}"
        await self._ensure_consumer_group(stream_name)
        logger.info(
            f"Worker '{self.consumer_name}' listening on workspace stream: {stream_name}"
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
                        self._dispatch(stream_name, event_id, event_data),
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
                logger.error(f"Worker stream read error: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def _dispatch(
        self, stream_name: str, event_id: bytes, event_data: dict
    ) -> None:
        """Process one Redis stream message in an isolated DB session.

        Workspace events are loaded from Redis once before opening the DB session and
        reused throughout the entire handling chain (seq computation, executor
        forwarding) to avoid repeated round-trips.  xack is sent only on
        success; failed messages re-enter the pending queue for retry.
        """
        try:
            event = self._parse_redis_event(event_data)
            workspace_id = event.workspace_id or ""
            if not workspace_id:
                # Try to extract from stream name if not in event payload
                parts = stream_name.split(":")
                if len(parts) >= 2:
                    workspace_id = parts[1]

            # Load all workspace events from Redis before opening the DB session.
            workspace_events: list[Event] = []
            if workspace_id:
                workspace_events = await self._load_workspace_events(workspace_id)

            async with get_session("structure") as db:
                publisher = EventPublisher(
                    db,
                    self.redis,
                    run_buffer_provider=self._get_run_buffer,
                    run_seq_cursor=self._run_seq_cursors,
                )
                state_machine = RunStateMachine(db, self.redis, publisher)
                ctx = _Ctx(db=db, publisher=publisher, state_machine=state_machine)
                await self.handle_event(event, ctx, workspace_events)

            await self.redis.xack(stream_name, REDIS_CONSUMER_GROUP, event_id)
        except Exception as e:
            logger.error("Failed to process message %s: %s", event_id, e, exc_info=True)
            # Not acked → enters pending queue for retry

    def prepare_executor(
        self,
        executor_code: str,
        app_config: dict | None = None,
        user_tool_classes: list | None = None,
        workspace_id: str = "",
        run_id: str = "",
        user_id: str = "",
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
        config["user_id"] = user_id

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
                user_id=str(run.user_id) if run.user_id else "",
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
        self, event: Event, ctx: _Ctx, workspace_events: list[Event]
    ) -> None:
        """Route events using the workspace's history for dispatch decisions.

        seq ends with "b" → TO_EXECUTOR        → _handle_to_executor
        seq ends with "0" → USER_MESSAGE       → run triggers + forward
        seq ends with "5" → TOOL_CALL          → handle_tool_call
        seq ends with [6789a] + has user msg   → forward to executor
        terminal in seq   → skip (run finished)
        otherwise         → skip (stale / unrecognised)
        """
        ctx.db.expire_all()

        try:
            code = encode(event.event_type)

            # ── Infrastructure: no seq needed ────────────────────────────────
            if RE_CODE_NOOP.match(code):
                return
            if code == "4":
                await handle_run_cancellation(event, self.runtime)
                return
            if RE_CODE_TASK.match(code):
                await handle_task_event(event)
                return
            if RE_CODE_ARTIFACT.match(code):
                await handle_artifact_event(event)
                return
            if RE_CODE_WORKSPACE.match(code):
                return

            run_id = event.run_id
            if not run_id:
                logger.debug("Event %s has no run_id, skipping", event.event_type)
                return

            # Skip trigger-sourced tool events (already executed by trigger processor).
            if (event.payload or {}).get(
                "_source"
            ) == "trigger" and RE_CODE_SKIP_SRC.match(code):
                return

            # Filter events for the current run to decide on routing
            run_events = [e for e in workspace_events if str(e.run_id) == str(run_id)]

            # Derive seq from pre-loaded events (no Redis call).
            seq = encode_sequence((str(e.event_type),) for e in run_events)
            logger.debug("handle_event: run=%s seq=%r", run_id, seq)

            match seq:
                case s if RE_TERMINAL.search(s):
                    logger.debug(
                        "Skipping %s for run %s: terminal (%s)",
                        event.event_type,
                        run_id,
                        s,
                    )

                case s if s.endswith("b"):
                    # Pass current-run events only — the executor recomputes sequence internally
                    await self._handle_to_executor(event, run_events, ctx)

                case s if s.endswith("0"):
                    await self._run_triggers(event, ctx)
                    await self._publish_to_executor_event(event, ctx)

                case s if s.endswith("5"):
                    run_tool_caller = self._run_tool_callers.get(str(run_id))
                    await handle_tool_call(
                        event,
                        ctx.db,
                        ctx.publisher,
                        ctx.state_machine,
                        tool_caller=run_tool_caller or self._tool_caller,
                        flush_run_buffer=self._flush_buffer_to_db,
                    )

                case s if RE_CODE_FORWARD.match(s[-1]) and RE_USER_MSG.search(s):
                    await self._publish_to_executor_event(event, ctx)

                case _:
                    logger.debug(
                        "No handler for event=%s run=%s seq=%s",
                        event.event_type,
                        run_id,
                        seq,
                    )

        except Exception as e:
            logger.error(
                "handle_event error for %s: %s", event.event_type, e, exc_info=True
            )
            if event.run_id:
                try:
                    run_id = (
                        event.run_id
                        if isinstance(event.run_id, UUID)
                        else UUID(str(event.run_id))
                    )
                    run_id_str = str(run_id)
                    buffered = self._pop_run_buffer(run_id_str)
                    if buffered:
                        ctx.db.add_all(buffered)
                    await ctx.state_machine.fail(run_id, error=str(e), auto_commit=True)
                    self._run_tool_callers.pop(run_id_str, None)
                    self.runtime.release(run_id)
                except Exception as fail_err:
                    logger.error("Failed to mark run as failed: %s", fail_err)

    async def _publish_to_executor_event(self, event: Event, ctx: _Ctx) -> None:
        """Publish a TO_EXECUTOR routing event to trigger executor dispatch.

        Carries only the executor_code so _get_or_create_executor can
        resolve the correct executor class for new runs.
        """
        await ctx.publisher.publish(
            event_type=EventType.TO_EXECUTOR,
            workspace_id=str(event.workspace_id),
            run_id=str(event.run_id) if event.run_id else None,
            payload={"_original_executor_code": event.executor_code},
            auto_commit=False,
        )
        logger.debug(
            "Published TO_EXECUTOR for original=%s run=%s",
            event.event_type,
            event.run_id,
        )

    async def _load_workspace_events(
        self, workspace_id: str, count: int = 2000
    ) -> list[Event]:
        """Load events for *workspace_id* from the Redis workspace stream."""
        workspace_stream = (
            f"{RE_CODE_WORKSPACE}:{workspace_id}:{REDIS_STREAM_EVENTS_SUFFIX}"
        )
        try:
            messages = await self.redis.xrange(workspace_stream, count=count)
        except Exception as e:
            logger.warning(
                "_load_workspace_events: redis error for workspace %s: %s",
                workspace_id,
                e,
            )
            return []

        return [Event.from_redis_fields(fields) for _, fields in messages]

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

        executor = await self._get_or_create_executor(envelope, ctx)
        if not executor:
            return None, "", False

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
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
                auto_commit=False,
            )
        except Exception as e:
            logger.error(f"Error publishing agent event for run {run_id}: {e}")
