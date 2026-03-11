# aiwen/core/interfaces/executor.py
"""Executor ABC and typed event-stream helpers.

Provides the ``Executor`` abstract base class that concrete executors
inherit from, along with the ``WaitingForTool`` exception used
across the streaming pipeline.

Consumers should depend on ``ExecutorProtocol`` (in ``protocols.py``),
not on this ABC directly — see the Dependency Inversion notes in the plan.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any, ClassVar
from uuid import uuid4

from aiwen.core.interfaces.protocols import ExecutorProtocol
from aiwen.core.interfaces.tool import ToolControlFlow
from aiwen.models.events import Event
from aiwen.schemas.events.event_payloads import (
    BaseEventSchema,
    EventType,
)


class WaitingForUserInput(ToolControlFlow):
    """Raised by a tool to pause execution and ask the user a question.

    Inherits from ``ToolControlFlow`` so that ``BaseTool.__call__`` lets it
    propagate without invoking ``on_error``.  The tool handler catches this,
    transitions the run to ``waiting`` state, and publishes an ``AGENT_QUERY``
    event so the frontend can display the question.  Execution resumes when the
    user submits a response via the ``/runs/{run_id}/feedback`` endpoint.

    ``question`` is the text to show the user.
    """

    def __init__(self, question: str):
        self.question = question
        super().__init__(f"Waiting for user input: {question[:80]}")


class WaitingForTool(Exception):
    """Raised when a tool requires human approval before execution.

    The worker catches this, persists ``info`` into ``Run.waiting_for``,
    and transitions the run to ``waiting``.  On resume the worker reads
    the stored snapshot + the user's approval decision and passes both
    back into the executor via ``input_data["_resumed"]``.

    ``info`` must contain:
      * ``type``           – ``"tool_approval"``
      * ``tool_name``      – name of the blocked tool
      * ``tool_id``        – correlation id
      * ``arguments``      – the arguments the LLM chose
      * ``ai_message``     – serialised AIMessage (via ``messages_to_dict``)
                             so the resume path can reconstruct the
                             conversation without re-invoking the LLM
      * ``executor_code``  – TEMPLATE["executor_code"] so the resume
                             endpoint knows which executor to re-dispatch
    """

    def __init__(self, info: dict[str, Any]):
        self.info = info
        super().__init__(f"Waiting for tool approval: {info.get('tool_name')}")


class Executor(ABC, ExecutorProtocol):
    """Abstract base class for every agent in the system.

    This is the INTERFACE/ABC for executor implementations. Do not confuse with:
    - ExecutorTemplate (aiwen.models.executor.executor) - ORM model for DB persistence
    - ExecutorInstanceManager (aiwen.services.executor.runtime) - manages running instances
    - ExecutorCRUD (aiwen.services.executor.executor_crud) - database operations

    Concrete executors (DefaultExecutor, ConflictExecutor, …) inherit from
    this class and get the ``_emit_*`` event-factory helpers for free.

    Consumers (Worker, Runtime, Factory) should type-hint with
    ``ExecutorProtocol`` from ``core.interfaces.protocols`` — **not** this
    class — so they depend only on the abstract interface.

    Subclasses must:
      * declare a ``TEMPLATE`` ClassVar (consumed by ``@register_executor``)
      * implement ``setup()``
      * override ``_process_*`` methods for events they want to handle

    All interactions with the executor happen through ``process_event()``.
    The old ``run()``, ``stream()``, and ``cancel()`` methods have been
    removed in favor of event-driven processing:
      * user.message event triggers agent execution
      * run.cancelled event triggers cleanup
      * executor emits agent.* events for streaming output
    """

    TEMPLATE: ClassVar[dict[str, Any]]

    # Re-export exceptions so ``except executor.WaitingForTool`` resolves on
    # instances (the worker currently references it that way).
    WaitingForTool = WaitingForTool
    WaitingForUserInput = WaitingForUserInput

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._token_index: int = 0
        self._event_queue: list[Event] = []
        # Per-event-invocation token accumulator (reset on each process_event call).
        self._current_input_tokens: int = 0
        self._current_output_tokens: int = 0

    # ── core contract ────────────────────────────────────────────
    @abstractmethod
    async def setup(self) -> None:
        """Setup any resources needed by the agent."""
        ...

    # ── event processing hooks ────────────────────────────────────
    # Override these in subclasses to react to incoming events.
    # Default implementations are no-ops so subclasses only need to
    # override the events they care about.

    async def process_event(
        self, event: Event
    ) -> AsyncGenerator[Event, None]:  # ty:ignore[invalid-method-override]
        """Dispatch an incoming event to the appropriate handler.

        Routes ``event`` to the matching ``_process_*`` method based on
        ``event.event_type``. Subclasses should override individual
        ``_process_*`` methods rather than this dispatcher.

        Yields:
            Event instances emitted by the handler via the _event_queue.
        """
        # Clear the event queue before processing
        self._event_queue.clear()

        # Call the handler

        # Yield all events that were emitted during processing
        for emitted_event in self._event_queue:
            yield emitted_event

    # -- User event handlers --

    async def _process_user_message(self, payload: dict[str, Any]) -> None:
        """Handle an incoming USER_MESSAGE event."""
        ...

    async def _process_user_feedback(self, payload: dict[str, Any]) -> None:
        """Handle an incoming USER_FEEDBACK event."""
        ...

    # -- Agent event handlers --

    async def _process_agent_token(self, payload: dict[str, Any]) -> None:
        """Handle an incoming AGENT_TOKEN event."""
        ...

    async def _process_agent_message(self, payload: dict[str, Any]) -> None:
        """Handle an incoming AGENT_MESSAGE event."""
        ...

    async def _process_agent_thinking(self, payload: dict[str, Any]) -> None:
        """Handle an incoming AGENT_THINKING event."""
        ...

    async def _process_agent_plan_step(self, payload: dict[str, Any]) -> None:
        """Handle an incoming AGENT_PLAN_STEP event."""
        ...

    async def _process_agent_query(self, payload: dict[str, Any]) -> None:
        """Handle an incoming AGENT_QUERY event."""
        ...

    async def _process_agent_heartbeat(self, payload: dict[str, Any]) -> None:
        """Handle an incoming AGENT_HEARTBEAT event."""
        ...

    # -- Tool event handlers --

    async def _process_tool_call(self, payload: dict[str, Any]) -> None:
        """Handle an incoming TOOL_CALL event."""
        ...

    async def _process_tool_result(self, payload: dict[str, Any]) -> None:
        """Handle an incoming TOOL_RESULT event."""
        ...

    async def _process_tool_error(self, payload: dict[str, Any]) -> None:
        """Handle an incoming TOOL_ERROR event."""
        ...

    async def _process_tool_pending(self, payload: dict[str, Any]) -> None:
        """Handle an incoming TOOL_PENDING event."""
        ...

    async def _process_tool_client_request(self, payload: dict[str, Any]) -> None:
        """Handle an incoming TOOL_CLIENT_REQUEST event."""
        ...

    # -- Context event handlers --

    async def _process_context_using(self, payload: dict[str, Any]) -> None:
        """Handle an incoming USING_CONTEXT event."""
        ...

    async def _process_context_put_outcome(self, payload: dict[str, Any]) -> None:
        """Handle an incoming PUT_OUTCOME event."""
        ...

    # -- Run lifecycle event handlers --

    async def _process_run_created(self, payload: dict[str, Any]) -> None:
        """Handle an incoming RUN_CREATED event."""
        ...

    async def _process_run_state_change(self, payload: dict[str, Any]) -> None:
        """Handle an incoming RUN_STATE_CHANGE event."""
        ...

    async def _process_run_completed(self, payload: dict[str, Any]) -> None:
        """Handle an incoming RUN_COMPLETED event."""
        ...

    async def _process_run_failed(self, payload: dict[str, Any]) -> None:
        """Handle an incoming RUN_FAILED event."""
        ...

    async def _process_run_cancelled(self, payload: dict[str, Any]) -> None:
        """Handle an incoming RUN_CANCELLED event."""
        ...

    # -- Workspace event handlers --

    async def _process_workspace_created(self, payload: dict[str, Any]) -> None:
        """Handle an incoming WORKSPACE_CREATED event."""
        ...

    async def _process_workspace_updated(self, payload: dict[str, Any]) -> None:
        """Handle an incoming WORKSPACE_UPDATED event."""
        ...

    async def _process_workspace_member_join(self, payload: dict[str, Any]) -> None:
        """Handle an incoming WORKSPACE_MEMBER_JOIN event."""
        ...

    async def _process_workspace_member_leave(self, payload: dict[str, Any]) -> None:
        """Handle an incoming WORKSPACE_MEMBER_LEAVE event."""
        ...

    async def _process_workspace_member_role_change(
        self, payload: dict[str, Any]
    ) -> None:
        """Handle an incoming WORKSPACE_MEMBER_ROLE_CHANGE event."""
        ...

    # -- Task event handlers --

    async def _process_task_create(self, payload: dict[str, Any]) -> None:
        """Handle an incoming TASK_CREATE event."""
        ...

    async def _process_task_update(self, payload: dict[str, Any]) -> None:
        """Handle an incoming TASK_UPDATE event."""
        ...

    async def _process_task_delete(self, payload: dict[str, Any]) -> None:
        """Handle an incoming TASK_DELETE event."""
        ...

    async def _process_task_complete(self, payload: dict[str, Any]) -> None:
        """Handle an incoming TASK_COMPLETE event."""
        ...

    async def _process_task_assign(self, payload: dict[str, Any]) -> None:
        """Handle an incoming TASK_ASSIGN event."""
        ...

    # -- Artifact event handlers --

    async def _process_artifact_create(self, payload: dict[str, Any]) -> None:
        """Handle an incoming ARTIFACT_CREATE event."""
        ...

    async def _process_artifact_update(self, payload: dict[str, Any]) -> None:
        """Handle an incoming ARTIFACT_UPDATE event."""
        ...

    async def _process_artifact_delete(self, payload: dict[str, Any]) -> None:
        """Handle an incoming ARTIFACT_DELETE event."""
        ...

    async def _process_artifact_version(self, payload: dict[str, Any]) -> None:
        """Handle an incoming ARTIFACT_VERSION event."""
        ...

    # -- System event handlers --

    async def _process_system_error(self, payload: dict[str, Any]) -> None:
        """Handle an incoming SYSTEM_ERROR event."""
        ...

    async def _process_system_notification(self, payload: dict[str, Any]) -> None:
        """Handle an incoming SYSTEM_NOTIFICATION event."""
        ...

    # -- Dispatch table (event_type.value -> handler method) --

    _EVENT_HANDLERS: ClassVar[dict[str, Any]] = {}  # populated after class body

    # ── token counter ────────────────────────────────────────────

    def _reset_token_index(self) -> None:
        """Reset the per-stream token counter.  Call at stream start."""
        self._token_index = 0

    def _reset_token_counters(self) -> None:
        """Reset per-event-invocation token accumulators.  Call at event entry."""
        self._current_input_tokens = 0
        self._current_output_tokens = 0

    def _accumulate_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Add tokens from one LLM call into the per-event accumulators."""
        self._current_input_tokens += input_tokens
        self._current_output_tokens += output_tokens

    # ── Event factory helpers ─────────────────────────────────────

    def _make_event(self, event_type: EventType, payload: dict[str, Any]) -> Event:
        """Create a transient Event for yielding from the executor.

        workspace_id is a placeholder — the worker overrides it when
        publishing via EventPublisher.publish().
        """
        return Event(event_type=event_type, workspace_id=uuid4(), payload=payload)

    def _emit_token(self, token: str, *, is_final: bool = False) -> Event:
        """Emit a streaming token event (AGENT_TOKEN)."""
        event = self._make_event(
            EventType.AGENT_TOKEN,
            {"token": token, "index": self._token_index, "is_final": is_final},
        )
        self._token_index += 1
        return event

    def _emit_message(
        self, content: str, *, context_breakdown: dict[str, Any] | None = None
    ) -> Event:
        """Emit a complete message event (AGENT_MESSAGE)."""
        payload: dict[str, Any] = {"content": content}
        if context_breakdown is not None:
            payload["_ctx"] = context_breakdown
        event = self._make_event(EventType.AGENT_MESSAGE, payload)
        event.input_tokens = self._current_input_tokens
        event.output_tokens = self._current_output_tokens
        return event

    def _emit_thinking(self, content: str) -> Event:
        """Emit a reasoning/thinking trace event (AGENT_THINKING)."""
        return self._make_event(EventType.AGENT_THINKING, {"content": content})

    def _emit_tool_call(
        self, tool_name: str, tool_id: str, arguments: dict[str, Any]
    ) -> Event:
        """Emit a single tool call event (TOOL_CALL)."""
        return self._make_event(
            EventType.TOOL_CALL,
            {"tool_name": tool_name, "tool_id": tool_id, "arguments": arguments},
        )

# Populate the dispatch table after the class body so all methods are defined.
# This mapping is auto-generated from EventType enum to ensure completeness.
def _build_event_handlers() -> dict[str, Any]:
    """Build the event handler dispatch table.

    Automatically maps each EventType enum value to its corresponding
    _process_* method. If a method is missing for an event type, this
    will raise an AttributeError at import time, ensuring completeness.

    Returns:
        dict mapping event_type string to handler method
    """
    handlers = {}
    for event_type in EventType:
        # Convert event type to method name: "user.message" -> "_process_user_message"
        method_name = f"_process_{event_type.value.replace('.', '_')}"
        try:
            handler = getattr(Executor, method_name)
            handlers[event_type.value] = handler
        except AttributeError:
            raise AttributeError(
                f"Missing event handler: Executor.{method_name}() for {event_type.value}. "
                f"All EventType enum values must have a corresponding _process_* method."
            ) from None
    return handlers


Executor._EVENT_HANDLERS = _build_event_handlers()
