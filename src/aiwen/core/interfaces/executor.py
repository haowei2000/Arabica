# aiwen/core/interfaces/executor.py
"""Executor ABC and typed event-stream helpers.

Provides the ``Executor`` abstract base class that concrete executors
inherit from, along with the ``AgentEvent`` dataclass and the
``WaitingForTool`` exception used across the streaming pipeline.

Consumers should depend on ``ExecutorProtocol`` (in ``protocols.py``),
not on this ABC directly — see the Dependency Inversion notes in the plan.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, ClassVar

from aiwen.schemas.context.tools import ToolClientRequestPayload
from aiwen.schemas.events.event_payloads import (
    AgentPlanEventSchema,
    AgentTokenEventSchema,
    EventType,
    RunStateChangeEventSchema,
    ToolCallEventSchema,
    ToolPendingEventSchema,
    ToolResultEventSchema,
    UserMessage,
    WorkspaceMemberJoinEventSchema,
)


@dataclass(frozen=True)
class AgentEvent:
    """A single typed event in the agent stream.

    Consumers serialise via ``to_dict()`` before pushing to Redis / SSE.
    The ``event_type`` value matches an ``EventType`` enum member and the
    ``payload`` structure matches the corresponding ``*Payload`` schema.
    """

    event_type: str  # EventType.value
    payload: dict[str, Any]  # validated *Payload.model_dump()

    def to_dict(self) -> dict[str, Any]:
        return {"event_type": self.event_type, "payload": self.payload}


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


class Executor(ABC):
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
      * implement ``setup()``, ``run()``
      * optionally override ``stream()`` and ``cancel()``
    """

    TEMPLATE: ClassVar[dict[str, Any]]

    # Re-export so ``except executor.WaitingForTool`` resolves on instances
    # (the worker currently references it that way).
    WaitingForTool = WaitingForTool

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._token_index: int = 0
        self._cancelled: bool = False

    # ── core contract ────────────────────────────────────────────
    @abstractmethod
    async def setup(self) -> None:
        """Setup any resources needed by the agent."""
        ...

    @abstractmethod
    async def run(self, user_message: UserMessage) -> dict[str, Any]:
        """Run to completion and return the final result."""
        ...

    async def cancel(self) -> None:
        """Request graceful cancellation of a running executor.

        The default implementation sets an internal flag.  Long-running
        loops (e.g. the agentic loop) should check ``self._cancelled``
        periodically.
        """
        self._cancelled = True

    async def stream(
        self, user_message: UserMessage,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Yield typed events while processing input.

        Tool dependencies are injected via the constructor (config),
        not passed as arguments to this method.

        The default implementation runs the agent to completion and emits
        a single ``AGENT_MESSAGE``.  Override for true streaming.
        """
        result = await self.run(user_message)
        yield self._emit_message(result.get("answer", str(result)))

    # ── event processing hooks ────────────────────────────────────
    # Override these in subclasses to react to incoming events.
    # Default implementations are no-ops so subclasses only need to
    # override the events they care about.

    async def process_event(self, event: AgentEvent) -> None:
        """Dispatch an incoming event to the appropriate handler.

        Routes ``event`` to the matching ``_process_*`` method based on
        ``event.event_type``. Subclasses should override individual
        ``_process_*`` methods rather than this dispatcher.
        """
        handler = self._EVENT_HANDLERS.get(event.event_type)
        if handler:
            await handler(self, event.payload)

    # -- User event handlers --

    async def _process_user_message(self, payload: dict[str, Any]) -> None:
        """Handle an incoming USER_MESSAGE event."""
        ...

    async def _process_user_feedback(self, payload: dict[str, Any]) -> None:
        """Handle an incoming USER_FEEDBACK event."""
        ...

    # -- Agent event handlers --

    async def _process_token(self, payload: dict[str, Any]) -> None:
        """Handle an incoming AGENT_TOKEN event."""
        ...

    async def _process_message(self, payload: dict[str, Any]) -> None:
        """Handle an incoming AGENT_MESSAGE event."""
        ...

    async def _process_thinking(self, payload: dict[str, Any]) -> None:
        """Handle an incoming AGENT_THINKING event."""
        ...

    async def _process_plan_step(self, payload: dict[str, Any]) -> None:
        """Handle an incoming AGENT_PLAN_STEP event."""
        ...

    async def _process_heartbeat(self, payload: dict[str, Any]) -> None:
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

    async def _process_using_context(self, payload: dict[str, Any]) -> None:
        """Handle an incoming USING_CONTEXT event."""
        ...

    async def _process_put_outcome(self, payload: dict[str, Any]) -> None:
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

    async def _process_workspace_member_role_change(self, payload: dict[str, Any]) -> None:
        """Handle an incoming WORKSPACE_MEMBER_ROLE_CHANGE event."""
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

    # ── event factories ──────────────────────────────────────────
    # Each method constructs an AgentEvent whose payload is validated
    # by the matching Pydantic schema before it is returned.

    def _emit_token(self, token: str, *, is_final: bool = False) -> AgentEvent:
        """``AGENT_TOKEN`` – one streaming chunk."""
        event = AgentEvent(
            event_type=EventType.AGENT_TOKEN.value,
            payload=AgentTokenEventSchema(
                token=token,
                token_index=self._token_index,
                is_final=is_final,
            ).model_dump(),
        )
        self._token_index += 1
        return event

    def _emit_message(self, content: str) -> AgentEvent:
        """``AGENT_MESSAGE`` – the complete text response."""
        return AgentEvent(
            event_type=EventType.AGENT_MESSAGE.value,
            payload={"content": content},
        )

    def _emit_thinking(self, content: str) -> AgentEvent:
        """``AGENT_THINKING`` – a reasoning / chain-of-thought trace."""
        return AgentEvent(
            event_type=EventType.AGENT_THINKING.value,
            payload={"content": content},
        )

    def _emit_plan_step(
        self,
        step_number: int,
        description: str,
        status: str = "pending",
        output: str | None = None,
    ) -> AgentEvent:
        """``AGENT_PLAN_STEP`` – one step in a multi-step plan."""
        return AgentEvent(
            event_type=EventType.AGENT_PLAN_STEP.value,
            payload=AgentPlanEventSchema(
                step_number=step_number,
                step_description=description,
                status=status,
                output=output,
            ).model_dump(),
        )

    def _emit_tool_call(
        self,
        tool_name: str,
        tool_id: str,
        arguments: dict[str, Any],
    ) -> AgentEvent:
        """``TOOL_CALL`` – the LLM has decided to invoke a tool."""
        return AgentEvent(
            event_type=EventType.TOOL_CALL.value,
            payload=ToolCallEventSchema(
                tool_name=tool_name,
                tool_id=tool_id,
                arguments=arguments,
            ).model_dump(),
        )

    def _emit_tool_result(
        self,
        tool_name: str,
        tool_id: str,
        result: Any,
        *,
        execution_time_ms: int | None = None,
    ) -> AgentEvent:
        """``TOOL_RESULT`` – a tool completed successfully."""
        return AgentEvent(
            event_type=EventType.TOOL_RESULT.value,
            payload=ToolResultEventSchema(
                tool_name=tool_name,
                tool_id=tool_id,
                result=result,
                success=True,
                execution_time_ms=execution_time_ms,
            ).model_dump(),
        )

    def _emit_tool_error(
        self,
        tool_name: str,
        tool_id: str,
        error_message: str,
    ) -> AgentEvent:
        """``TOOL_ERROR`` – a tool raised an exception."""
        return AgentEvent(
            event_type=EventType.TOOL_ERROR.value,
            payload=ToolResultEventSchema(
                tool_name=tool_name,
                tool_id=tool_id,
                result=None,
                success=False,
                error_message=error_message,
            ).model_dump(),
        )

    def _emit_tool_pending(
        self,
        tool_name: str,
        tool_id: str,
        arguments: dict[str, Any],
        reason: str = "requires_approval",
    ) -> AgentEvent:
        """``TOOL_PENDING`` – tool is blocked; waiting for human action."""
        return AgentEvent(
            event_type=EventType.TOOL_PENDING.value,
            payload=ToolPendingEventSchema(
                tool_name=tool_name,
                tool_id=tool_id,
                reason=reason,
                requires_approval=True,
                arguments=arguments,
            ).model_dump(),
        )

    def _emit_tool_client_request(
        self,
        tool_name: str,
        tool_id: str,
        handler: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: int = 120,
        config: dict[str, Any] | None = None,
    ) -> AgentEvent:
        """``TOOL_CLIENT_REQUEST`` – request client-side tool execution.

        This event is sent to the browser to trigger local execution
        (e.g., file picker, camera capture, clipboard access).
        """
        return AgentEvent(
            event_type=EventType.TOOL_CLIENT_REQUEST.value,
            payload=ToolClientRequestPayload(
                tool_name=tool_name,
                tool_id=tool_id,
                handler=handler,
                arguments=arguments,
                timeout_seconds=timeout_seconds,
                config=config or {},
            ).model_dump(),
        )

    # ── user event factories ──────────────────────────────────────

    def _emit_user_message(self, message: str, *, user_id: str | None = None) -> AgentEvent:
        """``USER_MESSAGE`` – echo of the user's input."""
        payload: dict[str, Any] = {"message": message}
        if user_id:
            payload["user_id"] = user_id
        return AgentEvent(event_type=EventType.USER_MESSAGE.value, payload=payload)

    def _emit_user_feedback(
        self, feedback: str, *, rating: int | None = None, user_id: str | None = None
    ) -> AgentEvent:
        """``USER_FEEDBACK`` – user feedback on a response."""
        payload: dict[str, Any] = {"feedback": feedback}
        if rating is not None:
            payload["rating"] = rating
        if user_id:
            payload["user_id"] = user_id
        return AgentEvent(event_type=EventType.USER_FEEDBACK.value, payload=payload)

    # ── agent event factories ─────────────────────────────────────

    def _emit_heartbeat(self, *, status: str = "alive", detail: str | None = None) -> AgentEvent:
        """``AGENT_HEARTBEAT`` – periodic liveness signal."""
        payload: dict[str, Any] = {"status": status}
        if detail:
            payload["detail"] = detail
        return AgentEvent(event_type=EventType.AGENT_HEARTBEAT.value, payload=payload)

    # ── context event factories ───────────────────────────────────

    def _emit_using_context(
        self,
        context_id: str,
        context_type: str,
        name: str,
        *,
        snippet: str | None = None,
    ) -> AgentEvent:
        """``USING_CONTEXT`` – agent is referencing a context entry."""
        payload: dict[str, Any] = {
            "context_id": context_id,
            "context_type": context_type,
            "name": name,
        }
        if snippet:
            payload["snippet"] = snippet
        return AgentEvent(event_type=EventType.USING_CONTEXT.value, payload=payload)

    def _emit_put_outcome(
        self,
        context_type: str,
        name: str,
        content: str,
        *,
        context_id: str | None = None,
    ) -> AgentEvent:
        """``PUT_OUTCOME`` – agent writes a result back into context."""
        payload: dict[str, Any] = {
            "context_type": context_type,
            "name": name,
            "content": content,
        }
        if context_id:
            payload["context_id"] = context_id
        return AgentEvent(event_type=EventType.PUT_OUTCOME.value, payload=payload)

    # ── run lifecycle event factories ─────────────────────────────

    def _emit_run_created(self, run_id: str, *, executor_code: str | None = None) -> AgentEvent:
        """``RUN_CREATED`` – a new run has been created."""
        payload: dict[str, Any] = {"run_id": run_id}
        if executor_code:
            payload["executor_code"] = executor_code
        return AgentEvent(event_type=EventType.RUN_CREATED.value, payload=payload)

    def _emit_run_state_change(
        self,
        previous_state: str,
        new_state: str,
        *,
        reason: str | None = None,
        triggered_by: str | None = None,
    ) -> AgentEvent:
        """``RUN_STATE_CHANGE`` – run transitioned between states."""
        return AgentEvent(
            event_type=EventType.RUN_STATE_CHANGE.value,
            payload=RunStateChangeEventSchema(
                previous_state=previous_state,
                new_state=new_state,
                reason=reason,
                triggered_by=triggered_by,
            ).model_dump(exclude={"event_type", "app_id", "workspace_id", "run_id", "executor_code"}),
        )

    def _emit_run_completed(
        self, run_id: str, *, result: dict[str, Any] | None = None
    ) -> AgentEvent:
        """``RUN_COMPLETED`` – run finished successfully."""
        payload: dict[str, Any] = {"run_id": run_id}
        if result:
            payload["result"] = result
        return AgentEvent(event_type=EventType.RUN_COMPLETED.value, payload=payload)

    def _emit_run_failed(
        self, run_id: str, error: str, *, error_type: str | None = None
    ) -> AgentEvent:
        """``RUN_FAILED`` – run encountered a fatal error."""
        payload: dict[str, Any] = {"run_id": run_id, "error": error}
        if error_type:
            payload["error_type"] = error_type
        return AgentEvent(event_type=EventType.RUN_FAILED.value, payload=payload)

    def _emit_run_cancelled(self, run_id: str, *, reason: str | None = None) -> AgentEvent:
        """``RUN_CANCELLED`` – run was cancelled."""
        payload: dict[str, Any] = {"run_id": run_id}
        if reason:
            payload["reason"] = reason
        return AgentEvent(event_type=EventType.RUN_CANCELLED.value, payload=payload)

    # ── workspace event factories ─────────────────────────────────

    def _emit_workspace_created(self, workspace_id: str, name: str) -> AgentEvent:
        """``WORKSPACE_CREATED`` – a new workspace was created."""
        return AgentEvent(
            event_type=EventType.WORKSPACE_CREATED.value,
            payload={"workspace_id": workspace_id, "name": name},
        )

    def _emit_workspace_updated(
        self, workspace_id: str, *, changes: dict[str, Any] | None = None
    ) -> AgentEvent:
        """``WORKSPACE_UPDATED`` – workspace metadata was modified."""
        payload: dict[str, Any] = {"workspace_id": workspace_id}
        if changes:
            payload["changes"] = changes
        return AgentEvent(event_type=EventType.WORKSPACE_UPDATED.value, payload=payload)

    def _emit_workspace_member_join(
        self,
        workspace_id: str,
        member_id: str,
        role: str,
        *,
        invited_by: str | None = None,
    ) -> AgentEvent:
        """``WORKSPACE_MEMBER_JOIN`` – a member joined the workspace."""
        return AgentEvent(
            event_type=EventType.WORKSPACE_MEMBER_JOIN.value,
            payload=WorkspaceMemberJoinEventSchema(
                member_id=member_id,
                role=role,
                invited_by=invited_by,
            ).model_dump(exclude={"event_type", "app_id", "workspace_id", "run_id", "executor_code"}),
        )

    def _emit_workspace_member_leave(
        self, workspace_id: str, member_id: str
    ) -> AgentEvent:
        """``WORKSPACE_MEMBER_LEAVE`` – a member left the workspace."""
        return AgentEvent(
            event_type=EventType.WORKSPACE_MEMBER_LEAVE.value,
            payload={"workspace_id": workspace_id, "member_id": member_id},
        )

    def _emit_workspace_member_role_change(
        self,
        workspace_id: str,
        member_id: str,
        old_role: str,
        new_role: str,
    ) -> AgentEvent:
        """``WORKSPACE_MEMBER_ROLE_CHANGE`` – a member's role was changed."""
        return AgentEvent(
            event_type=EventType.WORKSPACE_MEMBER_ROLE_CHANGE.value,
            payload={
                "workspace_id": workspace_id,
                "member_id": member_id,
                "old_role": old_role,
                "new_role": new_role,
            },
        )

    # ── system event factories ────────────────────────────────────

    def _emit_system_error(
        self, error: str, *, error_type: str | None = None, details: dict[str, Any] | None = None
    ) -> AgentEvent:
        """``SYSTEM_ERROR`` – a system-level error occurred."""
        payload: dict[str, Any] = {"error": error}
        if error_type:
            payload["error_type"] = error_type
        if details:
            payload["details"] = details
        return AgentEvent(event_type=EventType.SYSTEM_ERROR.value, payload=payload)

    def _emit_system_notification(
        self, message: str, *, level: str = "info", category: str | None = None
    ) -> AgentEvent:
        """``SYSTEM_NOTIFICATION`` – informational system notification."""
        payload: dict[str, Any] = {"message": message, "level": level}
        if category:
            payload["category"] = category
        return AgentEvent(event_type=EventType.SYSTEM_NOTIFICATION.value, payload=payload)


# Populate the dispatch table after the class body so all methods are defined.
Executor._EVENT_HANDLERS = {
    EventType.USER_MESSAGE.value: Executor._process_user_message,
    EventType.USER_FEEDBACK.value: Executor._process_user_feedback,
    EventType.AGENT_TOKEN.value: Executor._process_token,
    EventType.AGENT_MESSAGE.value: Executor._process_message,
    EventType.AGENT_THINKING.value: Executor._process_thinking,
    EventType.AGENT_PLAN_STEP.value: Executor._process_plan_step,
    EventType.AGENT_HEARTBEAT.value: Executor._process_heartbeat,
    EventType.TOOL_CALL.value: Executor._process_tool_call,
    EventType.TOOL_RESULT.value: Executor._process_tool_result,
    EventType.TOOL_ERROR.value: Executor._process_tool_error,
    EventType.TOOL_PENDING.value: Executor._process_tool_pending,
    EventType.TOOL_CLIENT_REQUEST.value: Executor._process_tool_client_request,
    EventType.USING_CONTEXT.value: Executor._process_using_context,
    EventType.PUT_OUTCOME.value: Executor._process_put_outcome,
    EventType.RUN_CREATED.value: Executor._process_run_created,
    EventType.RUN_STATE_CHANGE.value: Executor._process_run_state_change,
    EventType.RUN_COMPLETED.value: Executor._process_run_completed,
    EventType.RUN_FAILED.value: Executor._process_run_failed,
    EventType.RUN_CANCELLED.value: Executor._process_run_cancelled,
    EventType.WORKSPACE_CREATED.value: Executor._process_workspace_created,
    EventType.WORKSPACE_UPDATED.value: Executor._process_workspace_updated,
    EventType.WORKSPACE_MEMBER_JOIN.value: Executor._process_workspace_member_join,
    EventType.WORKSPACE_MEMBER_LEAVE.value: Executor._process_workspace_member_leave,
    EventType.WORKSPACE_MEMBER_ROLE_CHANGE.value: Executor._process_workspace_member_role_change,
    EventType.SYSTEM_ERROR.value: Executor._process_system_error,
    EventType.SYSTEM_NOTIFICATION.value: Executor._process_system_notification,
}
