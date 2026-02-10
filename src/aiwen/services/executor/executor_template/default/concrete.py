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
    messages_to_dict,
)

from aiwen.registries import ToolRegistry, register_executor
from aiwen.schemas.agents.app import AppConfig, Model
from aiwen.schemas.events.event_payloads import UserMessage
from aiwen.services.executor.base import AgentEvent, Executor, WaitingForTool
from aiwen.services.tools.inner_tool.browser_tools import BROWSER_TOOLS
from aiwen.services.tools.inner_tool.server_tools import (
    CONTEXT_TOOLS,
    SERVER_TOOLS,
    UTILITY_TOOLS,
)

logger = logging.getLogger(__name__)

# Characters needed to rule out a ``<think>`` opening tag.
_THINK_TAG = "<think>"
_THINK_TAG_LEN = len(_THINK_TAG)  # 7
_THINK_CLOSE = "</think>"


@register_executor
class DefaultAgentTemplate(Executor):
    """Default agent with conversation context and structured event streaming.

    默认Agent，支持对话历史和结构化事件流。
    """

    TEMPLATE: ClassVar[dict[str, Any]] = {
        "template_code": "SimpleAgent",
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
        # Server tools configuration - see _load_server_tools() for details
        self.server_tools_config = config.get("server_tools", False)
        # Tools in this list pause the run and ask the user before executing.
        self.approval_tools: list[str] = config.get("approval_tools", [])

        from aiwen.extensions.llm.llm import get_llm

        self.llm = get_llm(self.model_name, self.model_provider)

        # Collect all tools
        tools = []

        # Add browser tools if enabled (convert BaseTool classes to LangChain tools)
        if self.enable_browser_tools:
            for tool_class in BROWSER_TOOLS:
                lc_tool = self._convert_to_langchain_tool(tool_class)
                tools.append(lc_tool)
            logger.info(f"Loaded {len(BROWSER_TOOLS)} browser tools")

        # Add server tools based on configuration
        server_tool_classes = self._load_server_tools()
        # Convert BaseTool classes to LangChain tools
        for tool_class in server_tool_classes:
            lc_tool = self._convert_to_langchain_tool(tool_class)
            tools.append(lc_tool)

        # Add user-defined tools from ToolRegistry
        user_tools = self._load_registry_tools()
        tools.extend(user_tools)

        system_prompt = "You are a helpful Assistant "
        self.agent = create_agent(
            model=self.llm, tools=tools, system_prompt=system_prompt
        )

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

    # ── tool execution routing ───────────────────────────────────

    # ── context ──────────────────────────────────────────────────

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
        """Execute the agent and return the final answer."""

        messages = self._prepare_messages(user_message)
        response = await self.agent.ainvoke({"messages": messages})
        final_messages = response.get("messages", [])
        final_text = ""
        if final_messages and isinstance(final_messages[-1], AIMessage):
            final_text = final_messages[-1].content or ""
        return {"answer": final_text}

    # ── stream ───────────────────────────────────────────────────

    async def stream(
        self,
        user_message: UserMessage,
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

        messages = self._prepare_messages(user_message)
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
                    raise WaitingForTool(
                        {
                            "type": "tool_approval",
                            "tool_name": tool_name,
                            "tool_id": tool_id,
                            "arguments": arguments,
                            "ai_message": messages_to_dict([last_ai_output])[0],
                            "executor_code": self.TEMPLATE["template_code"],
                        }
                    )

                # All tools execute directly through BaseTool's __call__() method
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
