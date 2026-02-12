#!/usr/bin/env python3
"""Default Executor – conversation context + structured event stream.

Implements a manual agentic loop with tool calling through injected
abstractions (``ToolProvider`` / ``ToolCaller``).  The executor never
imports concrete tool registries or tool modules directly – all
dependencies are injected via config by the Worker (composition root).

Tool approval (HITL) flow
-------------------------
When a tool in ``approval_tools`` is called, the executor:
  1. Serialises the full conversation state (``messages``) and the
     remaining unprocessed tool calls.
  2. Emits ``TOOL_PENDING`` and raises ``WaitingForTool``.
  3. The worker persists the info, transitions the run to *waiting*.
  4. On resume the executor reconstructs ``messages``, handles the
     approved/rejected tool, processes any remaining tool calls, and
     continues the agentic loop.

Streaming event map:
    LLM streaming        ->  AGENT_THINKING  (inside <think> block)
                          ->  AGENT_TOKEN     (normal response text)
    LLM final response   ->  AGENT_MESSAGE   (no tool calls)
    Tool execution       ->  TOOL_CALL -> TOOL_RESULT / TOOL_ERROR
    Tool approval        ->  TOOL_PENDING  (run paused)
"""

from collections.abc import AsyncGenerator
import json
import logging
import time
from typing import Any, ClassVar

from langchain_core.messages import (
    AIMessage,
    ToolMessage,
    messages_to_dict,
)

from aiwen.core.interfaces.tool_service import ToolCaller, ToolProvider
from aiwen.registries import register_executor
from aiwen.registries.base_class.base_executor import (
    AgentEvent,
    Executor,
    WaitingForTool,
)
from aiwen.schemas.app import AppConfig
from aiwen.schemas.events.event_payloads import UserMessage
from aiwen.schemas.llm.chat_llm import ChatLLM

logger = logging.getLogger(__name__)

# Characters needed to rule out a ``<think>`` opening tag.
_THINK_TAG = "<think>"
_THINK_TAG_LEN = len(_THINK_TAG)  # 7
_THINK_CLOSE = "</think>"


@register_executor
class DefaultExecutor(Executor):
    """Default agent with conversation context and structured event streaming.

    Uses a manual agentic loop: the LLM is called with tool schemas bound
    via ``bind_tools()``.  When the LLM response contains tool calls, each
    tool is executed via the injected ``ToolCaller`` abstraction.  Tool
    results are fed back to the LLM as ``ToolMessage`` instances and the
    loop continues until the LLM produces a final text response (no tool
    calls).

    Dependency Inversion:
      - ``ToolProvider``  – provides available tool classes (injected via config)
      - ``ToolCaller``    – executes a tool by name (injected via config)
    """

    TEMPLATE: ClassVar[dict[str, Any]] = {
        "template_code": "SimpleAgent",
        "template_name": "Default Detection Agent",
        "enabled": True,
        "version": 1,
        "config": AppConfig(
            model=ChatLLM(provider="tongyi", name="qwen-plus"), context=None
        ),
    }

    def __init__(self, config: dict):
        super().__init__(config)
        self.model_provider = config.get("model_provider", "tongyi")
        self.model_name = config.get("model_name", "qwen-plus")
        self.max_history_messages = config.get("max_history_messages", 20)
        # Tools in this list pause the run and ask the user before executing.
        self.approval_tools: list[str] = config.get("approval_tools", [])
        # Maximum tool-call iterations before forcing a stop.
        self.max_iterations: int = config.get("max_iterations", 10)

        # ── Dependency-injected abstractions ─────────────────────
        # The Worker injects concrete implementations; the executor
        # only depends on the ToolProvider / ToolCaller protocols.
        self.tool_provider: ToolProvider | None = config.get("tool_provider")
        self.tool_caller: ToolCaller | None = config.get("tool_caller")

        from aiwen.extensions.llm.llm import get_llm

        self.llm = get_llm(self.model_name, self.model_provider)

        # Collect tool definitions (LangChain StructuredTool objects)
        # used to inform the LLM which tools are available.
        lc_tools: list = self._load_tools()

        self.system_prompt = "You are a helpful Assistant "

        # Bind tool schemas to the LLM so it knows which tools are available.
        # Tool *execution* goes through the ToolCaller at runtime, not through
        # LangChain's agent loop.
        if lc_tools:
            self.llm_with_tools = self.llm.bind_tools(lc_tools)
        else:
            self.llm_with_tools = self.llm

    # ── setup ────────────────────────────────────────────────────

    async def setup(self) -> None:
        """Setup any resources needed by the agent.

        For this template, all setup is done synchronously in __init__.
        This method is provided to satisfy the Executor abstract interface.
        """
        pass

    # ── tool loading (via injected ToolProvider) ────────────────

    def _load_tools(self) -> list:
        """Load tools from the injected ToolProvider and convert to LangChain format.

        If no ``tool_provider`` was injected, returns an empty list.

        Returns:
            list: LangChain StructuredTool instances for binding to the LLM.
        """
        if self.tool_provider is None:
            logger.warning("No ToolProvider injected; executor has no tools")
            return []

        lc_tools: list = []
        for tool_class in self.tool_provider.get_tool_classes():
            try:
                lc_tool = self._convert_to_langchain_tool(tool_class)
                lc_tools.append(lc_tool)
            except Exception as e:
                name = getattr(getattr(tool_class, "METADATA", None), "name", tool_class)
                logger.error(f"Failed to convert tool {name}: {e}", exc_info=True)

        logger.info(f"Loaded {len(lc_tools)} tools via ToolProvider")
        return lc_tools

    def _convert_to_langchain_tool(self, tool_class: type):
        """Convert BaseTool class to LangChain-compatible tool.

        Args:
            tool_class: BaseTool subclass (either class or instance)

        Returns:
            LangChain StructuredTool
        """
        from langchain_core.tools import StructuredTool

        # If it's a class, instantiate it
        if isinstance(tool_class, type):
            tool_instance = tool_class()
            metadata = tool_class.METADATA
            input_schema = tool_class.InputSchema
        else:
            # Already an instance
            tool_instance = tool_class
            metadata = tool_instance.METADATA
            input_schema = tool_instance.InputSchema

        # Create LangChain StructuredTool
        lc_tool = StructuredTool(
            name=metadata.name,
            description=metadata.description,
            func=lambda **kwargs: None,  # Sync placeholder
            coroutine=tool_instance.__call__,  # Use async call
            args_schema=input_schema,
        )

        return lc_tool

    # ── tool execution via ToolRegistry ──────────────────────────

    async def _execute_tool_call(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a tool call via the injected ToolCaller.

        Args:
            tool_name: Name of the tool (matches ``METADATA.name``).
            arguments: Arguments to pass to the tool.

        Returns:
            dict: Tool execution result from ``ToolCaller.call()``.

        Raises:
            ValueError: If no ToolCaller was injected or tool not found.
        """
        if self.tool_caller is None:
            raise ValueError("No ToolCaller injected; cannot execute tools")

        return await self.tool_caller.call(tool_name, arguments)

    # ── message serialization for HITL state persistence ─────────

    @staticmethod
    def _serialize_messages(messages: list) -> list[dict[str, Any]]:
        """Serialize a mixed messages list to JSON-compatible format.

        Handles both dict-format messages (system/user) and LangChain
        message objects (AIMessage, ToolMessage).
        """
        result: list[dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, AIMessage):
                result.append({
                    "_type": "ai",
                    "content": msg.content,
                    "tool_calls": msg.tool_calls,
                })
            elif isinstance(msg, ToolMessage):
                result.append({
                    "_type": "tool",
                    "content": msg.content,
                    "tool_call_id": msg.tool_call_id,
                })
            elif isinstance(msg, dict):
                result.append({"_type": "dict", "data": msg})
            else:
                # Fallback for other LangChain message types
                result.append({
                    "_type": "dict",
                    "data": {"role": "unknown", "content": str(msg)},
                })
        return result

    @staticmethod
    def _deserialize_messages(serialized: list[dict[str, Any]]) -> list:
        """Reconstruct messages list from serialized format."""
        result: list = []
        for msg in serialized:
            msg_type = msg.get("_type", "dict")
            if msg_type == "ai":
                result.append(AIMessage(
                    content=msg.get("content", ""),
                    tool_calls=msg.get("tool_calls", []),
                ))
            elif msg_type == "tool":
                result.append(ToolMessage(
                    content=msg.get("content", ""),
                    tool_call_id=msg.get("tool_call_id", ""),
                ))
            elif msg_type == "dict":
                result.append(msg.get("data", {}))
        return result

    # ── tool call processing (shared by stream & resume) ─────────

    async def _process_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        messages: list,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Execute a list of tool calls, yielding events and appending results.

        If a tool requires approval, raises ``WaitingForTool`` with the
        full conversation state so the run can be resumed later.

        Args:
            tool_calls: Tool call dicts from ``AIMessage.tool_calls``.
            messages: The live conversation messages list (mutated in-place).

        Yields:
            TOOL_CALL, TOOL_RESULT, TOOL_ERROR, or TOOL_PENDING events.

        Raises:
            WaitingForTool: When a tool in ``approval_tools`` is encountered.
        """
        for idx, tool_call in enumerate(tool_calls):
            tc_name = tool_call["name"]
            tc_id = tool_call["id"]
            tc_args = tool_call.get("args", {})

            # ── HITL approval gate ───────────────────────────────
            if tc_name in self.approval_tools:
                # Collect the tool calls that haven't been processed yet
                # (the current one + everything after it).
                remaining = tool_calls[idx + 1:]

                yield self._emit_tool_pending(
                    tool_name=tc_name,
                    tool_id=tc_id,
                    arguments=tc_args,
                )
                raise WaitingForTool(
                    {
                        "type": "tool_approval",
                        "tool_name": tc_name,
                        "tool_id": tc_id,
                        "arguments": tc_args,
                        "messages": self._serialize_messages(messages),
                        "remaining_tool_calls": remaining,
                        "executor_code": self.TEMPLATE["template_code"],
                    }
                )

            yield self._emit_tool_call(
                tool_name=tc_name,
                tool_id=tc_id,
                arguments=tc_args,
            )

            start_time = time.time()
            try:
                result = await self._execute_tool_call(tc_name, tc_args)
                elapsed_ms = int((time.time() - start_time) * 1000)
                yield self._emit_tool_result(
                    tool_name=tc_name,
                    tool_id=tc_id,
                    result=result,
                    execution_time_ms=elapsed_ms,
                )
                messages.append(
                    ToolMessage(
                        content=json.dumps(
                            result, ensure_ascii=False, default=str
                        ),
                        tool_call_id=tc_id,
                    )
                )
            except Exception as e:
                elapsed_ms = int((time.time() - start_time) * 1000)
                error_msg = str(e)
                yield self._emit_tool_error(
                    tool_name=tc_name,
                    tool_id=tc_id,
                    error_message=error_msg,
                )
                messages.append(
                    ToolMessage(
                        content=json.dumps(
                            {"success": False, "error": error_msg},
                            ensure_ascii=False,
                        ),
                        tool_call_id=tc_id,
                    )
                )

    # ── message conversion ───────────────────────────────────────

    def _prepare_messages(self, user_message: UserMessage | dict) -> list:
        """Convert UserMessage to LangChain message format.

        Accepts both ``UserMessage`` Pydantic model and raw dict (the
        worker may pass either depending on whether the run is fresh or
        resumed).

        Args:
            user_message: Input message (UserMessage or dict)

        Returns:
            list: LangChain-compatible message list
        """
        if isinstance(user_message, dict):
            message = user_message.get("message", "")
        else:
            message = user_message.message

        # If already a list, use as-is
        if isinstance(message, list):
            return message

        # If string, convert to LangChain format
        if isinstance(message, str):
            return [{"role": "user", "content": message}]

        # Fallback: try to use as-is
        return [message]

    # ── resume handling ──────────────────────────────────────────

    async def _handle_resume(
        self,
        user_message: dict,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Handle a resumed run after tool approval / rejection.

        Reconstructs the conversation from stored state, processes the
        approved or rejected tool, handles any remaining tool calls, then
        continues the normal agentic loop.

        Args:
            user_message: Dict containing ``_resumed``, ``_waiting_info``,
                and ``_approval`` keys injected by the worker.

        Yields:
            AgentEvent instances for the entire remainder of the run.
        """
        waiting_info: dict = user_message.get("_waiting_info", {})
        approval: dict = user_message.get("_approval", {})
        approved: bool = approval.get("approved", True)

        tool_name: str = waiting_info.get("tool_name", "")
        tool_id: str = waiting_info.get("tool_id", "")
        tool_args: dict = waiting_info.get("arguments", {})

        # ── reconstruct conversation state ───────────────────────
        stored = waiting_info.get("messages", [])
        messages: list = self._deserialize_messages(stored)

        # ── handle the approved / rejected tool ──────────────────
        if approved:
            yield self._emit_tool_call(
                tool_name=tool_name,
                tool_id=tool_id,
                arguments=tool_args,
            )
            start_time = time.time()
            try:
                result = await self._execute_tool_call(tool_name, tool_args)
                elapsed_ms = int((time.time() - start_time) * 1000)
                yield self._emit_tool_result(
                    tool_name=tool_name,
                    tool_id=tool_id,
                    result=result,
                    execution_time_ms=elapsed_ms,
                )
                messages.append(
                    ToolMessage(
                        content=json.dumps(
                            result, ensure_ascii=False, default=str
                        ),
                        tool_call_id=tool_id,
                    )
                )
            except Exception as e:
                elapsed_ms = int((time.time() - start_time) * 1000)
                error_msg = str(e)
                yield self._emit_tool_error(
                    tool_name=tool_name,
                    tool_id=tool_id,
                    error_message=error_msg,
                )
                messages.append(
                    ToolMessage(
                        content=json.dumps(
                            {"success": False, "error": error_msg},
                            ensure_ascii=False,
                        ),
                        tool_call_id=tool_id,
                    )
                )
        else:
            # Tool rejected by user
            yield self._emit_tool_error(
                tool_name=tool_name,
                tool_id=tool_id,
                error_message="Tool call rejected by user",
            )
            messages.append(
                ToolMessage(
                    content=json.dumps(
                        {"success": False, "error": "Tool call rejected by user"},
                        ensure_ascii=False,
                    ),
                    tool_call_id=tool_id,
                )
            )

        # ── process remaining tool calls from the same AI turn ───
        remaining = waiting_info.get("remaining_tool_calls", [])
        if remaining:
            async for event in self._process_tool_calls(remaining, messages):
                yield event

        # ── continue the normal agentic loop ─────────────────────
        async for event in self._agentic_loop(messages):
            yield event

    # ── agentic loop (core streaming logic) ──────────────────────

    async def _agentic_loop(
        self,
        messages: list,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run the agentic loop: call LLM, process tool calls, repeat.

        This is the core streaming logic extracted so it can be called
        both from ``stream()`` (fresh run) and ``_handle_resume()``
        (resumed run after approval).

        Args:
            messages: The conversation messages list (mutated in-place).

        Yields:
            AgentEvent instances.
        """
        for _iteration in range(self.max_iterations):
            # ── per-iteration state ──────────────────────────────
            response_buf = ""  # accumulates response text
            think_buf = ""  # accumulates content inside <think>
            # None = haven't seen enough tokens to decide yet
            # True  = currently inside a <think> block
            # False = past any possible <think> block
            in_thinking: bool | None = None
            full_response = None  # accumulated AIMessageChunk

            # ── stream LLM response tokens ───────────────────────
            async for chunk in self.llm_with_tools.astream(messages):
                # Accumulate chunks into a complete response
                if full_response is None:
                    full_response = chunk
                else:
                    full_response = full_response + chunk

                token: str = chunk.content or ""
                if not token:
                    continue

                # --- thinking-block detection ---------------------
                if in_thinking is None:
                    think_buf += token
                    # Can the buffer still be the start of <think>?
                    if not _THINK_TAG.startswith(think_buf[:_THINK_TAG_LEN]):
                        # Definitely not a thinking stream - flush
                        in_thinking = False
                        response_buf = think_buf
                        yield self._emit_token(think_buf)
                        think_buf = ""
                    elif _THINK_TAG in think_buf:
                        # Opening tag complete - enter thinking mode
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

            # ── flush remaining buffers ──────────────────────────
            if in_thinking is None and think_buf:
                response_buf = think_buf
                yield self._emit_token(think_buf)
                think_buf = ""
            elif in_thinking is True and think_buf:
                yield self._emit_thinking(think_buf)
                think_buf = ""

            # ── process accumulated response ─────────────────────
            if full_response is None:
                break

            # Build a proper AIMessage for the conversation history
            ai_message = AIMessage(
                content=full_response.content or "",
                tool_calls=getattr(full_response, "tool_calls", None) or [],
            )
            messages.append(ai_message)

            # ── no tool calls -> final text response ─────────────
            if not ai_message.tool_calls:
                yield self._emit_token("", is_final=True)
                yield self._emit_message(ai_message.content or response_buf)
                break

            # ── execute tool calls (may raise WaitingForTool) ────
            async for event in self._process_tool_calls(
                ai_message.tool_calls, messages
            ):
                yield event

            # Reset token index for the next LLM call iteration
            self._reset_token_index()

        else:
            # for/else: max_iterations reached without a final text response
            yield self._emit_token("", is_final=True)
            yield self._emit_message(
                "Maximum tool-calling iterations reached. Stopping."
            )

    # ── run (non-streaming) ──────────────────────────────────────

    async def run(self, user_message: UserMessage | dict) -> dict[str, Any]:
        """Execute the agent loop and return the final answer.

        Calls the LLM in a loop, executing tool calls through the ToolRegistry
        until the LLM produces a final text response without tool calls.

        Note: HITL approval is only supported in ``stream()`` mode.  In
        ``run()`` mode approval-listed tools are executed without pausing
        (the caller is expected to use ``stream()`` for interactive runs).
        """
        messages: list = [
            {"role": "system", "content": self.system_prompt},
        ]
        messages.extend(self._prepare_messages(user_message))

        for _ in range(self.max_iterations):
            response: AIMessage = await self.llm_with_tools.ainvoke(messages)
            messages.append(response)

            # If no tool calls, return the final text
            if not response.tool_calls:
                return {"answer": response.content or ""}

            # Execute each tool call through the ToolRegistry
            for tool_call in response.tool_calls:
                tc_name = tool_call["name"]
                tc_id = tool_call["id"]
                tc_args = tool_call.get("args", {})

                try:
                    result = await self._execute_tool_call(tc_name, tc_args)
                    messages.append(
                        ToolMessage(
                            content=json.dumps(
                                result, ensure_ascii=False, default=str
                            ),
                            tool_call_id=tc_id,
                        )
                    )
                except Exception as e:
                    messages.append(
                        ToolMessage(
                            content=json.dumps(
                                {"success": False, "error": str(e)},
                                ensure_ascii=False,
                            ),
                            tool_call_id=tc_id,
                        )
                    )

        return {"answer": "Maximum tool-calling iterations reached."}

    # ── stream ───────────────────────────────────────────────────

    async def stream(
        self,
        user_message: UserMessage | dict,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Stream typed events from the agentic loop.

        Supports two entry modes:

        1. **Fresh run** – ``user_message`` is a ``UserMessage`` (or plain
           dict with a ``message`` key).  The executor builds the initial
           messages list and enters the agentic loop.

        2. **Resumed run** – ``user_message`` is a dict with
           ``_resumed=True``.  The executor reconstructs the conversation
           from the stored state, handles the approved/rejected tool, and
           continues the agentic loop.

        Thinking-block detection
        ------------------------
        Reasoning models (e.g. Qwen3 with thinking enabled) wrap their
        chain-of-thought in ``<think>...</think>``.  The tag may arrive
        split across multiple tokens, so the first few tokens are buffered
        until we can decide whether the stream starts with the tag.
        """
        self._reset_token_index()

        # ── resumed run (after tool approval) ────────────────────
        if isinstance(user_message, dict) and user_message.get("_resumed"):
            async for event in self._handle_resume(user_message):
                yield event
            return

        # ── fresh run ────────────────────────────────────────────
        messages: list = [
            {"role": "system", "content": self.system_prompt},
        ]
        messages.extend(self._prepare_messages(user_message))

        async for event in self._agentic_loop(messages):
            yield event
