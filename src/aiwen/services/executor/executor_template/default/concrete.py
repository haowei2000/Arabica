#!/usr/bin/env python3
"""Default Executor – conversation context + structured event stream.

Implements a manual agentic loop with tool calling through the ToolRegistry.
When the LLM outputs tool calls, the executor looks up each tool by name
in the ToolRegistry and executes it directly via ``BaseTool.__call__()``,
then feeds the results back to the LLM as ``ToolMessage`` instances.

Streaming event map:
    LLM streaming        ->  AGENT_THINKING  (inside <think> block)
                          ->  AGENT_TOKEN     (normal response text)
    LLM final response   ->  AGENT_MESSAGE   (no tool calls)
    Tool execution       ->  TOOL_CALL -> TOOL_RESULT / TOOL_ERROR
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

from aiwen.registries import ToolRegistry, register_executor
from aiwen.registries.base_class.base_executor import (
    AgentEvent,
    Executor,
    WaitingForTool,
)
from aiwen.schemas.app import AppConfig
from aiwen.schemas.events.event_payloads import UserMessage
from aiwen.schemas.llm.chat_llm import ChatLLM
from aiwen.services.context.tools.browser_tools import BROWSER_TOOLS

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
    tool is looked up in the **ToolRegistry** and executed directly via
    ``BaseTool.__call__()``.  Tool results are fed back to the LLM as
    ``ToolMessage`` instances and the loop continues until the LLM produces
    a final text response (no tool calls).
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
        self.enable_browser_tools = config.get("enable_browser_tools", True)
        # Server tools configuration - see _load_server_tools() for details
        self.server_tools_config = config.get("server_tools", False)
        # Tools in this list pause the run and ask the user before executing.
        self.approval_tools: list[str] = config.get("approval_tools", [])
        # Maximum tool-call iterations before forcing a stop.
        self.max_iterations: int = config.get("max_iterations", 10)

        from aiwen.extensions.llm.llm import get_llm

        self.llm = get_llm(self.model_name, self.model_provider)

        # Collect tool definitions (LangChain StructuredTool objects)
        # used to inform the LLM which tools are available.
        lc_tools: list = []

        # Add browser tools if enabled
        if self.enable_browser_tools:
            for tool_class in BROWSER_TOOLS:
                lc_tool = self._convert_to_langchain_tool(tool_class)
                lc_tools.append(lc_tool)
            logger.info(f"Loaded {len(BROWSER_TOOLS)} browser tools")

        # Add server tools based on configuration
        server_tool_classes = self._load_server_tools()
        for tool_class in server_tool_classes:
            lc_tool = self._convert_to_langchain_tool(tool_class)
            lc_tools.append(lc_tool)

        # Add user-defined tools from ToolRegistry
        user_tools = self._load_registry_tools()
        lc_tools.extend(user_tools)

        self.system_prompt = "You are a helpful Assistant "

        # Bind tool schemas to the LLM so it knows which tools are available.
        # Tool *execution* goes through the ToolRegistry at runtime, not through
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

    # ── server tool loading ──────────────────────────────────────

    def _load_server_tools(self) -> list:
        """Load server-side tools based on configuration.

        Configuration options:
        1. False (default): No server tools
        2. True: All server tools (CONTEXT + FILE + UTILITY)
        3. List of strings: Specific tool groups ["context", "file", "utility"]
        4. Dict with group keys: {"context": True, "file": False, "utility": True}

        Returns:
            list: Selected server tools
        """
        config = self.server_tools_config

        # Case 1: Disabled
        if config is False or config is None:
            return []

        # Case 2: Enable all
        if config is True:
            logger.info("Loading all server tools")
            return list(SERVER_TOOLS)

        # Case 3: List of tool groups
        if isinstance(config, list):
            tools = []
            for group_name in config:
                group_name = group_name.lower()
                if group_name == "context":
                    tools.extend(CONTEXT_TOOLS)
                    logger.info("Loaded CONTEXT_TOOLS")
                elif group_name == "file":
                    tools.extend(FILE_TOOLS)
                    logger.info("Loaded FILE_TOOLS")
                elif group_name == "utility":
                    tools.extend(UTILITY_TOOLS)
                    logger.info("Loaded UTILITY_TOOLS")
                elif group_name == "all":
                    tools.extend(SERVER_TOOLS)
                    logger.info("Loaded all SERVER_TOOLS")
                else:
                    logger.warning(f"Unknown tool group: {group_name}")
            return tools

        # Case 4: Dict with fine-grained control
        if isinstance(config, dict):
            tools = []
            if config.get("context", False):
                tools.extend(CONTEXT_TOOLS)
                logger.info("Loaded CONTEXT_TOOLS")
            if config.get("file", False):
                tools.extend(FILE_TOOLS)
                logger.info("Loaded FILE_TOOLS")
            if config.get("utility", False):
                tools.extend(UTILITY_TOOLS)
                logger.info("Loaded UTILITY_TOOLS")
            return tools

        logger.warning(
            f"Invalid server_tools config: {config}, using default (no tools)"
        )
        return []

    # ── user tool loading ────────────────────────────────────────

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

    def _load_registry_tools(self) -> list:
        """Load tools from ToolRegistry and convert to LangChain format.

        Returns:
            list: LangChain-compatible tool instances
        """
        langchain_tools = []

        # Get all registered tool names
        tool_names = ToolRegistry.list_tools()

        for tool_name in tool_names:
            try:
                # Get tool class and instance
                tool_class = ToolRegistry.get_tool_class(tool_name)
                tool_instance = ToolRegistry.get_tool_instance(tool_name)

                if not tool_class or not tool_instance:
                    logger.warning(f"Tool {tool_name} not found in registry")
                    continue

                # Convert to LangChain tool
                lc_tool = self._convert_to_langchain_tool(tool_instance)
                langchain_tools.append(lc_tool)
                logger.info(f"Loaded tool from registry: {tool_name}")

            except Exception as e:
                logger.error(f"Failed to load tool {tool_name}: {e}", exc_info=True)

        return langchain_tools

    # ── tool execution via ToolRegistry ──────────────────────────

    async def _execute_tool_call(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a tool call by looking it up in the ToolRegistry.

        All tools (browser, server, user-defined) are registered via
        ``@register_tool`` and can be retrieved from the ToolRegistry by name.

        Args:
            tool_name: Name of the tool (matches ``METADATA.name``).
            arguments: Arguments to pass to the tool.

        Returns:
            dict: Tool execution result from ``BaseTool.__call__()``.

        Raises:
            ValueError: If the tool is not found in the registry.
        """
        tool_instance = ToolRegistry.get_tool_instance(tool_name)
        if tool_instance is not None:
            return await tool_instance(**arguments)

        raise ValueError(f"Tool '{tool_name}' not found in ToolRegistry")

    # ── message conversion ───────────────────────────────────────

    def _prepare_messages(self, user_message: UserMessage) -> list:
        """Convert UserMessage to LangChain message format.

        Args:
            user_message: Input message (can be string or list)

        Returns:
            list: LangChain-compatible message list
        """
        message = user_message.message

        # If already a list, use as-is
        if isinstance(message, list):
            return message

        # If string, convert to LangChain format
        if isinstance(message, str):
            return [{"role": "user", "content": message}]

        # Fallback: try to use as-is
        return [message]

    # ── run (non-streaming) ──────────────────────────────────────

    async def run(self, user_message: UserMessage) -> dict[str, Any]:
        """Execute the agent loop and return the final answer.

        Calls the LLM in a loop, executing tool calls through the ToolRegistry
        until the LLM produces a final text response without tool calls.
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
        user_message: UserMessage,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Stream typed events from the agentic loop.

        Streams LLM response tokens, detects tool calls in the accumulated
        response, executes them through the ToolRegistry, feeds results back,
        and continues until the LLM produces a final text response.

        Thinking-block detection
        ------------------------
        Reasoning models (e.g. Qwen3 with thinking enabled) wrap their
        chain-of-thought in ``<think>...</think>``.  The tag may arrive
        split across multiple tokens, so the first few tokens are buffered
        until we can decide whether the stream starts with the tag.

        * If it does  -> buffer until ``</think>``, emit one
          ``AGENT_THINKING`` event, then stream the rest as normal tokens.
        * If it doesn't -> flush the buffer as ``AGENT_TOKEN`` events with
          essentially zero additional latency (decision made as soon as the
          accumulated prefix can no longer match ``<think>``).

        The state resets on every loop iteration so that each LLM call
        is handled independently.
        """
        self._reset_token_index()

        messages: list = [
            {"role": "system", "content": self.system_prompt},
        ]
        messages.extend(self._prepare_messages(user_message))

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

            # ── execute tool calls through ToolRegistry ──────────
            for tool_call in ai_message.tool_calls:
                tc_name = tool_call["name"]
                tc_id = tool_call["id"]
                tc_args = tool_call.get("args", {})

                # ── HITL approval gate ───────────────────────────
                if tc_name in self.approval_tools:
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
                            "ai_message": messages_to_dict([ai_message])[0],
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

            # Reset token index for the next LLM call iteration
            self._reset_token_index()

        else:
            # for/else: max_iterations reached without a final text response
            yield self._emit_token("", is_final=True)
            yield self._emit_message(
                "Maximum tool-calling iterations reached. Stopping."
            )
