# aiwen/services/agent/base.py
"""Base agent class and typed event-stream protocol."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from aiwen.schemas.context.tools import ToolClientRequestPayload
from aiwen.schemas.events.event_payloads import (
    AgentPlanEvent,
    AgentTokenEvent,
    EventType,
    ToolCallEvent,
    ToolPendingEvent,
    ToolResultEvent,
    UserMessage,
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
      * ``executor_code``  – TEMPLATE["template_code"] so the resume
                             endpoint knows which executor to re-dispatch
    """

    def __init__(self, info: dict[str, Any]):
        self.info = info
        super().__init__(f"Waiting for tool approval: {info.get('tool_name')}")


class Executor(Protocol):
    """Abstract base for every agent in the system.

    Subclasses must:
      * declare a ``TEMPLATE`` ClassVar (consumed by ``@register_agent``)
      * implement ``run()``
      * optionally override ``stream()`` – the default wraps ``run()``
        into a single ``AGENT_MESSAGE`` event

    The ``_emit_*`` helpers build correctly-typed ``AgentEvent`` instances
    whose payloads are validated by the matching Pydantic schemas before
    serialisation.
    """

    TEMPLATE: ClassVar[dict[str, Any]]

    # Re-export so ``except executor.WaitingForTool`` resolves on instances
    # (the worker currently references it that way).
    WaitingForTool = WaitingForTool

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._token_index: int = 0

    # ── core contract ────────────────────────────────────────────
    @abstractmethod
    async def setup(self) -> None:
        """Setup any resources needed by the agent."""
        ...

    @abstractmethod
    async def run(self, user_message: UserMessage) -> dict[str, Any]:
        """Run to completion and return the final result."""
        ...

    async def stream(
        self, user_message: UserMessage | dict,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Yield typed events while processing input.

        Tool dependencies are injected via the constructor (config),
        not passed as arguments to this method.

        The default implementation runs the agent to completion and emits
        a single ``AGENT_MESSAGE``.  Override for true streaming.
        """
        result = await self.run(user_message)
        yield self._emit_message(result.get("answer", str(result)))

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
            payload=AgentTokenEvent(
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
            payload=AgentPlanEvent(
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
            payload=ToolCallEvent(
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
            payload=ToolResultEvent(
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
            payload=ToolResultEvent(
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
            payload=ToolPendingEvent(
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
