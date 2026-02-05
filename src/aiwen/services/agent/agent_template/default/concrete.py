#!/usr/bin/env python3
"""Default Agent Template – conversation context + structured event stream.

Streaming event map (LangGraph ``astream_events`` → project schema):
    on_chat_model_stream  →  AGENT_THINKING  (while inside a <think> block)
                          →  AGENT_TOKEN     (normal response text)
    on_chat_model_end     →  AGENT_MESSAGE   (final response only; skipped on
                                              intermediate tool-call steps)
    on_tool_start         →  TOOL_CALL
    on_tool_end           →  TOOL_RESULT
    on_tool_error         →  TOOL_ERROR

默认Agent模版，支持会话上下文和结构化事件流。
"""

from collections.abc import AsyncGenerator
import logging
import time
from typing import Any, ClassVar

from langchain.agents import create_agent
from langchain_core.messages import (
    AIMessage,
    ToolMessage,
    convert_to_messages,
    messages_to_dict,
)

from aiwen.schemas.agents.app import AppConfig, Model
from aiwen.schemas.agents.input import TextInput
from aiwen.schemas.tools.execution import ExecutionContext
from aiwen.services.agent.agent_registry import register_agent
from aiwen.services.agent.base import AgentEvent, BaseAgentTemplate, WaitingForTool
from aiwen.services.agent.tools import BROWSER_TOOLS
from aiwen.services.agent.tools.execution_mode import (
    ToolExecutionMode,
    get_tool_metadata,
)
from aiwen.services.agent.tools.execution_router import ExecutionRouter

from .context import get_messages_from_context

logger = logging.getLogger(__name__)

# Characters needed to rule out a ``<think>`` opening tag.
_THINK_TAG = "<think>"
_THINK_TAG_LEN = len(_THINK_TAG)  # 7
_THINK_CLOSE = "</think>"


@register_agent
class DefaultAgentTemplate(BaseAgentTemplate):
    """Default agent with conversation context and structured event streaming.

    默认Agent，支持对话历史和结构化事件流。
    """

    TEMPLATE: ClassVar[dict[str, Any]] = {
        "template_code": "DEFAULT001",
        "template_name": "Default Detection Agent",
        "enabled": True,
        "version": 1,
        "config": AppConfig(
            model=Model(provider="tongyi", name="qwen-plus"), context=None
        ),
    }

    def __init__(self, config: dict):
        super().__init__(config)
        self.model_provider = config.get("model_provider", "tongyi")
        self.model_name = config.get("model_name", "qwen-plus")
        self.max_history_messages = config.get("max_history_messages", 20)
        self.enable_browser_tools = config.get("enable_browser_tools", True)
        # Tools in this list pause the run and ask the user before executing.
        self.approval_tools: list[str] = config.get("approval_tools", [])

        from aiwen.extensions.llm.llm import get_llm

        self.llm = get_llm(self.model_name, self.model_provider)

        tools = BROWSER_TOOLS if self.enable_browser_tools else []
        system_prompt = (
            "You can use browser tools to interact with websites when needed. "
            "Launch a session first, reuse the session_id for subsequent actions, "
            "and close the session when finished."
        )
        self.agent = create_agent(
            model=self.llm, tools=tools, system_prompt=system_prompt
        )

        # Initialize execution router for sandbox/client tool execution
        self._execution_router = ExecutionRouter()
        self._execution_router.register_tools_from_list(tools)

    # ── tool execution routing ───────────────────────────────────

    def _get_tool_execution_mode(self, tool_name: str) -> ToolExecutionMode:
        """Get the execution mode for a tool."""
        metadata = get_tool_metadata(tool_name)
        return metadata.execution_mode

    def _requires_special_execution(self, tool_name: str) -> bool:
        """Check if a tool requires sandbox or client execution."""
        mode = self._get_tool_execution_mode(tool_name)
        return mode in (ToolExecutionMode.SANDBOX, ToolExecutionMode.CLIENT)

    async def _execute_via_router(
        self,
        tool_name: str,
        tool_id: str,
        arguments: dict[str, Any],
        context: ExecutionContext,
    ) -> dict[str, Any]:
        """Execute a tool through the ExecutionRouter.

        This method is used for tools that require sandbox or client execution.
        Returns the tool result as a dict.
        """
        result = await self._execution_router.execute(
            tool_name=tool_name,
            tool_id=tool_id,
            arguments=arguments,
            context=context,
        )
        return {
            "success": result.success,
            "result": result.result,
            "error_message": result.error_message,
            "execution_time_ms": result.execution_time_ms,
            "execution_mode": result.execution_mode,
        }

    # ── context ──────────────────────────────────────────────────

    async def _prepare_messages(self, input_data: TextInput) -> list[dict[str, str]]:
        """Load conversation history and append the current user message."""
        messages: list[dict[str, str]] = []
        if input_data.conversation_id:
            messages.extend(
                await get_messages_from_context(
                    conversation_id=input_data.conversation_id,
                    max_messages=self.max_history_messages,
                )
            )
        messages.append({"role": "user", "content": input_data.query})
        return messages

    async def _build_resumed_messages(self, raw_input: dict) -> list:
        """Reconstruct the message list at the interruption point.

        Appends the AI message (with ``tool_calls``) and a ``ToolMessage``
        carrying the user's approval result so the LLM continues from
        where it left off – no re-decision required.
        """
        waiting_info = raw_input["_waiting_info"]
        approval = raw_input["_approval"]

        # Base conversation (history + user message)
        clean = {k: v for k, v in raw_input.items() if not k.startswith("_")}
        base_input = TextInput(**clean)
        messages: list = await self._prepare_messages(base_input)

        # Re-hydrate the AI message that contained the tool_call
        ai_msg = convert_to_messages([waiting_info["ai_message"]])[0]
        messages.append(ai_msg)

        # Inject the tool result (approved content or denial)
        tool_call_id = ai_msg.tool_calls[0]["id"]
        if approval["approved"]:
            content = str(approval.get("tool_result") or "Tool approved by user.")
        else:
            content = "Tool call was denied by the user."
        messages.append(ToolMessage(content=content, tool_call_id=tool_call_id))

        return messages

    # ── run (non-streaming) ──────────────────────────────────────

    async def run(self, input_data: TextInput | dict) -> dict[str, Any]:
        """Execute the agent and return the final answer."""
        if isinstance(input_data, dict):
            input_data = TextInput(**input_data)

        messages = await self._prepare_messages(input_data)
        response = await self.agent.ainvoke({"messages": messages})
        final_messages = response.get("messages", [])
        final_text = ""
        if final_messages and isinstance(final_messages[-1], AIMessage):
            final_text = final_messages[-1].content or ""
        return {"answer": final_text}

    # ── stream ───────────────────────────────────────────────────

    async def stream(
        self, input_data: TextInput | dict
    ) -> AsyncGenerator[AgentEvent, None]:
        """Stream typed events from the agentic loop.

        Thinking-block detection
        ────────────────────────
        Reasoning models (e.g. Qwen3 with thinking enabled) wrap their
        chain-of-thought in ``<think>...</think>``.  The tag may arrive
        split across multiple tokens, so the first few tokens are buffered
        until we can decide whether the stream starts with the tag.

        * If it does  → buffer until ``</think>``, emit one
          ``AGENT_THINKING`` event, then stream the rest as normal tokens.
        * If it doesn't → flush the buffer as ``AGENT_TOKEN`` events with
          essentially zero additional latency (decision made as soon as the
          accumulated prefix can no longer match ``<think>``).

        The state resets on every ``on_chat_model_end`` so that each
        iteration of the agentic loop is handled independently.
        """
        # ── resume path: reconstruct messages at the interruption point ──
        if isinstance(input_data, dict) and input_data.get("_resumed"):
            messages = await self._build_resumed_messages(input_data)
        else:
            if isinstance(input_data, dict):
                input_data = TextInput(**input_data)
            messages = await self._prepare_messages(input_data)
        self._reset_token_index()

        # ── per-LLM-call state (reset on on_chat_model_end) ──────
        response_buf = ""  # accumulates response text for AGENT_MESSAGE
        think_buf = ""  # accumulates content inside <think>
        # None = haven't seen enough tokens to decide yet
        # True  = currently inside a <think> block
        # False = past any possible <think> block
        in_thinking: bool | None = None

        # tool_id → wall-clock start; used to compute execution_time_ms
        tool_start_times: dict[str, float] = {}
        # Snapshot of the latest AIMessage – needed so the HITL gate in
        # on_tool_start can serialise it for the resume path.
        last_ai_output: AIMessage | None = None

        async for event in self.agent.astream_events({"messages": messages}):
            event_name: str = event["event"]

            # ── streaming token ──────────────────────────────────
            if event_name == "on_chat_model_stream":
                token: str = event["data"]["chunk"].content
                if not token:
                    continue

                # --- thinking-block detection ---------------------
                if in_thinking is None:
                    think_buf += token
                    # Can the buffer still be the start of <think>?
                    if not _THINK_TAG.startswith(think_buf[:_THINK_TAG_LEN]):
                        # Definitely not a thinking stream – flush
                        in_thinking = False
                        response_buf = think_buf
                        yield self._emit_token(think_buf)
                        think_buf = ""
                    elif _THINK_TAG in think_buf:
                        # Opening tag complete – enter thinking mode
                        in_thinking = True
                        think_buf = think_buf.split(_THINK_TAG, 1)[1]
                        # Closing tag may already be present
                        if _THINK_CLOSE in think_buf:
                            content, rest = think_buf.split(_THINK_CLOSE, 1)
                            yield self._emit_thinking(content)
                            in_thinking = False
                            think_buf = ""
                            if rest:
                                response_buf += rest
                                yield self._emit_token(rest)
                    # else: still accumulating a possible prefix
                    continue

                if in_thinking is True:
                    think_buf += token
                    if _THINK_CLOSE in think_buf:
                        content, rest = think_buf.split(_THINK_CLOSE, 1)
                        yield self._emit_thinking(content)
                        in_thinking = False
                        think_buf = ""
                        if rest:
                            response_buf += rest
                            yield self._emit_token(rest)
                    continue  # still accumulating thinking content

                # --- normal response token ----------------------
                response_buf += token
                yield self._emit_token(token)

            # ── model finished one response ─────────────────────
            elif event_name == "on_chat_model_end":
                output = event["data"]["output"]
                last_ai_output = output
                # Emit AGENT_MESSAGE only for the final text response;
                # skip intermediate steps that end with tool calls.
                if not getattr(output, "tool_calls", None):
                    yield self._emit_token("", is_final=True)
                    yield self._emit_message(output.content or response_buf)
                # Reset state for the next iteration of the agentic loop
                response_buf = ""
                in_thinking = None
                think_buf = ""

            # ── tool lifecycle ──────────────────────────────────
            elif event_name == "on_tool_start":
                tool_id: str = event["run_id"]
                tool_name: str = event["name"]
                arguments: dict = event["data"].get("input", {})

                # ── HITL approval gate ──────────────────────────────
                if tool_name in self.approval_tools:
                    yield self._emit_tool_pending(
                        tool_name=tool_name,
                        tool_id=tool_id,
                        arguments=arguments,
                    )
                    raise WaitingForTool({
                        "type": "tool_approval",
                        "tool_name": tool_name,
                        "tool_id": tool_id,
                        "arguments": arguments,
                        "ai_message": messages_to_dict([last_ai_output])[0],
                        "executor_code": self.TEMPLATE["template_code"],
                    })

                # ── Client tool execution gate ─────────────────────
                tool_mode = self._get_tool_execution_mode(tool_name)
                if tool_mode == ToolExecutionMode.CLIENT:
                    metadata = get_tool_metadata(tool_name)
                    yield self._emit_tool_client_request(
                        tool_name=tool_name,
                        tool_id=tool_id,
                        handler=metadata.client_handler or tool_name,
                        arguments=arguments,
                        timeout_seconds=metadata.timeout_seconds,
                        config=metadata.client_config,
                    )
                    # Pause execution waiting for client response
                    raise WaitingForTool({
                        "type": "client_tool",
                        "tool_name": tool_name,
                        "tool_id": tool_id,
                        "arguments": arguments,
                        "ai_message": messages_to_dict([last_ai_output])[0],
                        "executor_code": self.TEMPLATE["template_code"],
                    })

                tool_start_times[tool_id] = time.time()
                yield self._emit_tool_call(
                    tool_name=tool_name,
                    tool_id=tool_id,
                    arguments=arguments,
                )

            elif event_name == "on_tool_end":
                tool_id = event["run_id"]
                start = tool_start_times.pop(tool_id, None)
                elapsed_ms = int((time.time() - start) * 1000) if start else None
                yield self._emit_tool_result(
                    tool_name=event["name"],
                    tool_id=tool_id,
                    result=event["data"].get("output", ""),
                    execution_time_ms=elapsed_ms,
                )

            elif event_name == "on_tool_error":
                tool_id = event["run_id"]
                tool_start_times.pop(tool_id, None)
                yield self._emit_tool_error(
                    tool_name=event["name"],
                    tool_id=tool_id,
                    error_message=str(event["data"].get("output", "")),
                )
