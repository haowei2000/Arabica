#!/usr/bin/env python3
"""Default Executor – conversation context + structured event stream.

Implements a manual agentic loop with tool calling through injected
abstractions (``ToolProvider`` / ``ToolCaller``).  The executor never
imports concrete tool registries or tool modules directly – all
dependencies are injected via config by the Worker (composition root).

Tool calling is delegated to a ``ToolCallingStrategy`` which can be
either ``FunctionCallingStrategy`` (OpenAI-compatible ``tools`` param)
or ``PromptCallingStrategy`` (tool descriptions in system prompt +
XML-tagged output parsing).

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

from aiwen.core.interfaces import (
    AgentEvent,
    Executor,
    ToolCaller,
    ToolProvider,
    WaitingForTool,
)
from aiwen.frameworks.tool_calling import (
    ChatMessage,
    FunctionCallingStrategy,
    LLMResponse,
    PromptCallingStrategy,
    ToolCallingStrategy,
    ToolCallRequest,
)
from aiwen.registries.core import register_executor
from aiwen.schemas.app import AppConfig
from aiwen.schemas.events.event_payloads import UserMessage
from aiwen.schemas.llm.chat_llm import ChatLLM

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """\
You are a helpful AI assistant.

## Session

- workspace_id: {workspace_id}
- run_id: {run_id}

## MANDATORY First Step

**Before responding to any user request**, you MUST call the following tool first:

```
list_context(workspace_id="{workspace_id}", path="./skills", mode="descendants", level="glance")
```

This retrieves all skills defined for this workspace. Read them carefully — they contain \
domain knowledge, instructions, and behavioural rules you must follow throughout the conversation. \
Do not skip this step even if the user's request seems straightforward.

## Context Tool

You have access to a workspace context store via the following operations.
All operations belong to the same **Context Tool** and require `workspace_id`.

```
glance_context | read_context | list_context | tree_context
glob_context   | search_context
create_context | update_context | delete_context
```

Each context entry has three disclosure levels: `glance` → `overview` → `detail`.
Start with `glance`, go deeper only when needed.

> To see everything available in this workspace, call:
> `list_context(workspace_id="{workspace_id}", path="./", mode="descendants", level="glance")`
"""

# Characters needed to rule out a ``<think>`` opening tag.
_THINK_TAG = "<think>"
_THINK_TAG_LEN = len(_THINK_TAG)  # 7
_THINK_CLOSE = "</think>"


@register_executor
class DefaultExecutor(Executor):
    """Default agent with conversation context and structured event streaming.

    Uses a manual agentic loop with a pluggable ``ToolCallingStrategy``.
    When the LLM response contains tool calls, each tool is executed via
    the injected ``ToolCaller`` abstraction.  Tool results are fed back
    and the loop continues until the LLM produces a final text response.

    Dependency Inversion:
      - ``ToolProvider``  – provides available tool classes (injected via config)
      - ``ToolCaller``    – executes a tool by name (injected via config)

    Config keys:
      - ``tool_calling_mode`` – ``"function_calling"`` (default) or
        ``"prompt_calling"``.
    """

    TEMPLATE: ClassVar[dict[str, Any]] = {
        "executor_code": "SimpleAgent",
        "executor_name": "SimpleAgent",
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
        self.tool_provider: ToolProvider | None = config.get("tool_provider")
        self.tool_caller: ToolCaller | None = config.get("tool_caller")

        # ── LLM connection info ──────────────────────────────────
        self._api_key, self._base_url = self._resolve_llm_config()

        # ── Tool calling strategy ────────────────────────────────
        mode = config.get("tool_calling_mode", "function_calling")
        self.strategy: ToolCallingStrategy = self._create_strategy(mode)

        # Collect tool classes and pre-format them for the strategy.
        tool_classes = self._collect_tool_classes()
        self.tools_info: Any = self.strategy.format_tools(tool_classes)

        self.workspace_id: str = config.get("workspace_id", "")
        run_id: str = config.get("run_id", "")
        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(workspace_id=self.workspace_id, run_id=run_id)

    # ── setup ────────────────────────────────────────────────────

    async def setup(self) -> None:
        """Setup any resources needed by the agent.

        For this template, all setup is done synchronously in __init__.
        This method is provided to satisfy the Executor abstract interface.
        """
        pass

    # ── internal helpers ─────────────────────────────────────────

    def _resolve_llm_config(self) -> tuple[str, str]:
        """Return ``(api_key, base_url)`` for the configured provider."""
        from aiwen.config.factory import get_settings

        settings = get_settings()
        match self.model_provider:
            case "tongyi":
                if settings.openai:
                    api_key = settings.openai.api_key or settings.dashscope_api_key
                    base_url = settings.openai.base_url
                else:
                    api_key = settings.dashscope_api_key
                    base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            case "ollama":
                api_key = "ollama"  # Ollama doesn't require a real key
                base_url = (
                    settings.ollama.base_url + "/v1"
                    if settings.ollama
                    else "http://127.0.0.1:11434/v1"
                )
            case _:
                raise ValueError(f"Unsupported provider: {self.model_provider}")
        return api_key, base_url

    @staticmethod
    def _create_strategy(mode: str) -> ToolCallingStrategy:
        """Instantiate the appropriate strategy for the given mode."""
        if mode == "prompt_calling":
            return PromptCallingStrategy()
        return FunctionCallingStrategy()

    # ── tool loading (via injected ToolProvider) ────────────────

    def _collect_tool_classes(self) -> list[type]:
        """Collect tool classes from the injected ToolProvider.

        Returns:
            list: BaseTool subclasses.
        """
        if self.tool_provider is None:
            logger.warning("No ToolProvider injected; executor has no tools")
            return []

        classes: list[type] = []
        for tool_class in self.tool_provider.get_tool_classes():
            try:
                # Ensure it is a class (not an instance).
                if not isinstance(tool_class, type):
                    tool_class = type(tool_class)
                classes.append(tool_class)
            except Exception as e:
                name = getattr(getattr(tool_class, "METADATA", None), "name", tool_class)
                logger.error(f"Failed to collect tool {name}: {e}", exc_info=True)

        logger.info(f"Collected {len(classes)} tool classes via ToolProvider")
        return classes

    # ── tool execution via ToolCaller ────────────────────────────

    async def _execute_tool_call(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a tool call via the injected ToolCaller."""
        if self.tool_caller is None:
            raise ValueError("No ToolCaller injected; cannot execute tools")
        return await self.tool_caller.call(tool_name, arguments)

    # ── message serialization for HITL state persistence ─────────

    @staticmethod
    def _serialize_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
        """Serialize the ChatMessage list to JSON-compatible format."""
        return [msg.to_dict() for msg in messages]

    @staticmethod
    def _deserialize_messages(serialized: list[dict[str, Any]]) -> list[ChatMessage]:
        """Reconstruct the ChatMessage list from a serialized format."""
        return [ChatMessage.from_dict(d) for d in serialized]

    # ── tool call processing (shared by stream & resume) ─────────

    async def _process_tool_calls(
        self,
        tool_calls: list[ToolCallRequest] | list[dict[str, Any]],
        messages: list[ChatMessage],
    ) -> AsyncGenerator[AgentEvent, None]:
        """Execute a list of tool calls, yielding events and appending results.

        Accepts either ``ToolCallRequest`` objects or raw dicts (for
        backward compatibility with serialized remaining_tool_calls).
        """
        # Normalize to ToolCallRequest
        normalized: list[ToolCallRequest] = []
        for tc in tool_calls:
            if isinstance(tc, ToolCallRequest):
                normalized.append(tc)
            elif isinstance(tc, dict):
                normalized.append(
                    ToolCallRequest(
                        id=tc.get("id", ""),
                        name=tc["name"],
                        arguments=tc.get("arguments", tc.get("args", {})),
                    )
                )
            else:
                normalized.append(tc)

        for idx, tc in enumerate(normalized):
            # ── HITL approval gate ───────────────────────────────
            if tc.name in self.approval_tools:
                remaining = [
                    {"id": r.id, "name": r.name, "arguments": r.arguments}
                    for r in normalized[idx + 1:]
                ]

                yield self._emit_tool_pending(
                    tool_name=tc.name,
                    tool_id=tc.id,
                    arguments=tc.arguments,
                )
                raise WaitingForTool(
                    {
                        "type": "tool_approval",
                        "tool_name": tc.name,
                        "tool_id": tc.id,
                        "arguments": tc.arguments,
                        "messages": self._serialize_messages(messages),
                        "remaining_tool_calls": remaining,
                        "executor_code": self.TEMPLATE["executor_code"],
                    }
                )

            yield self._emit_tool_call(
                tool_name=tc.name,
                tool_id=tc.id,
                arguments=tc.arguments,
            )

            start_time = time.time()
            try:
                result = await self._execute_tool_call(tc.name, tc.arguments)
                elapsed_ms = int((time.time() - start_time) * 1000)
                yield self._emit_tool_result(
                    tool_name=tc.name,
                    tool_id=tc.id,
                    result=result,
                    execution_time_ms=elapsed_ms,
                )
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=json.dumps(
                            result, ensure_ascii=False, default=str
                        ),
                        tool_call_id=tc.id,
                    )
                )
            except Exception as e:
                elapsed_ms = int((time.time() - start_time) * 1000)
                error_msg = str(e)
                yield self._emit_tool_error(
                    tool_name=tc.name,
                    tool_id=tc.id,
                    error_message=error_msg,
                )
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=json.dumps(
                            {"success": False, "error": error_msg},
                            ensure_ascii=False,
                        ),
                        tool_call_id=tc.id,
                    )
                )

    # ── message conversion ───────────────────────────────────────

    def _prepare_messages(self, user_message: UserMessage | dict) -> list[ChatMessage]:
        """Convert UserMessage to ChatMessage list.

        Accepts both ``UserMessage`` Pydantic model and raw dict (the
        worker may pass either depending on whether the run is fresh or
        resumed).
        """
        if isinstance(user_message, dict):
            message = user_message.get("message", "")
        else:
            message = user_message.message

        if isinstance(message, list):
            # Already a list of message dicts — convert to ChatMessage
            return [
                ChatMessage.from_dict(m) if isinstance(m, dict) else m
                for m in message
            ]

        if isinstance(message, str):
            return [ChatMessage(role="user", content=message)]

        return [ChatMessage(role="user", content=str(message))]

    # ── resume handling ──────────────────────────────────────────

    async def _handle_resume(
        self,
        user_message: dict,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Handle a resumed run after tool approval / rejection."""
        waiting_info: dict = user_message.get("_waiting_info", {})
        approval: dict = user_message.get("_approval", {})
        approved: bool = approval.get("approved", True)

        tool_name: str = waiting_info.get("tool_name", "")
        tool_id: str = waiting_info.get("tool_id", "")
        tool_args: dict = waiting_info.get("arguments", {})

        # ── reconstruct conversation state ───────────────────────
        stored = waiting_info.get("messages", [])
        messages: list[ChatMessage] = self._deserialize_messages(stored)

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
                    ChatMessage(
                        role="tool",
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
                    ChatMessage(
                        role="tool",
                        content=json.dumps(
                            {"success": False, "error": error_msg},
                            ensure_ascii=False,
                        ),
                        tool_call_id=tool_id,
                    )
                )
        else:
            yield self._emit_tool_error(
                tool_name=tool_name,
                tool_id=tool_id,
                error_message="Tool call rejected by user",
            )
            messages.append(
                ChatMessage(
                    role="tool",
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
        messages: list[ChatMessage],
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run the agentic loop: call LLM, process tool calls, repeat."""
        for _iteration in range(self.max_iterations):
            # ── per-iteration state ──────────────────────────────
            response_buf = ""
            think_buf = ""
            in_thinking: bool | None = None
            llm_response: LLMResponse | None = None

            # ── stream LLM response tokens ───────────────────────
            stream = self.strategy.call_llm_stream(
                messages,
                self.tools_info,
                model=self.model_name,
                api_key=self._api_key,
                base_url=self._base_url,
            )
            async for item in stream:
                if isinstance(item, LLMResponse):
                    # Final aggregated response
                    llm_response = item
                    continue

                # this item is a text token (str)
                token: str = item
                if not token:
                    continue

                # --- thinking-block detection ---------------------
                if in_thinking is None:
                    think_buf += token
                    if not _THINK_TAG.startswith(think_buf[:_THINK_TAG_LEN]):
                        in_thinking = False
                        response_buf = think_buf
                        yield self._emit_token(think_buf)
                        think_buf = ""
                    elif _THINK_TAG in think_buf:
                        in_thinking = True
                        think_buf = think_buf.split(_THINK_TAG, 1)[1]
                        if _THINK_CLOSE in think_buf:
                            content, rest = think_buf.split(_THINK_CLOSE, 1)
                            yield self._emit_thinking(content)
                            in_thinking = False
                            think_buf = ""
                            if rest:
                                response_buf += rest
                                yield self._emit_token(rest)
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
                    continue

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
            if llm_response is None:
                break

            # Build a ChatMessage for the conversation history
            ai_message = ChatMessage(
                role="assistant",
                content=llm_response.content or "",
                tool_calls=llm_response.tool_calls or None,
            )
            messages.append(ai_message)

            # ── no tool calls -> final text response ─────────────
            if not llm_response.tool_calls:
                yield self._emit_token("", is_final=True)
                yield self._emit_message(llm_response.content or response_buf)
                break

            # ── execute tool calls (may raise WaitingForTool) ────
            async for event in self._process_tool_calls(
                llm_response.tool_calls, messages
            ):
                yield event

            # Reset token index for the next LLM call iteration
            self._reset_token_index()

        else:
            yield self._emit_token("", is_final=True)
            yield self._emit_message(
                "Maximum tool-calling iterations reached. Stopping."
            )

    # ── run (non-streaming) ──────────────────────────────────────

    async def run(self, user_message: UserMessage | dict) -> dict[str, Any]:
        """Execute the agent loop and return the final answer.

        Note: HITL approval is only supported in ``stream()`` mode.
        """
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=self.system_prompt),
        ]
        messages.extend(self._prepare_messages(user_message))

        for _ in range(self.max_iterations):
            response: LLMResponse = await self.strategy.call_llm(
                messages,
                self.tools_info,
                model=self.model_name,
                api_key=self._api_key,
                base_url=self._base_url,
            )

            ai_message = ChatMessage(
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls or None,
            )
            messages.append(ai_message)

            if not response.tool_calls:
                return {"answer": response.content or ""}

            for tc in response.tool_calls:
                try:
                    result = await self._execute_tool_call(tc.name, tc.arguments)
                    messages.append(
                        ChatMessage(
                            role="tool",
                            content=json.dumps(
                                result, ensure_ascii=False, default=str
                            ),
                            tool_call_id=tc.id,
                        )
                    )
                except Exception as e:
                    messages.append(
                        ChatMessage(
                            role="tool",
                            content=json.dumps(
                                {"success": False, "error": str(e)},
                                ensure_ascii=False,
                            ),
                            tool_call_id=tc.id,
                        )
                    )

        return {"answer": "Maximum tool-calling iterations reached."}

    # ── history loading ──────────────────────────────────────────

    async def _load_history(self) -> list[ChatMessage]:
        """Load previous conversation turns from WorkspaceContext.

        Reads ``{workspace_id}/history/*`` entries ordered by creation time
        and reconstructs them as ChatMessage pairs (user + assistant).

        Returns an empty list if workspace_id is unset or no history exists.
        """
        if not self.workspace_id:
            return []

        try:
            from aiwen.extensions.database import get_session
            from aiwen.frameworks.context import DetailLevel
            from aiwen.utils.workspace_context_cache import get_cached_workspace_context

            async with get_session("aiwen") as db:
                service = await get_cached_workspace_context(db, self.workspace_id)
                results = await service.descendants(f"{self.workspace_id}/history")
                history_entries = results.disclose_all(DetailLevel.DETAIL)

            messages: list[ChatMessage] = []
            for entry in history_entries:
                detail = entry.get("detail")
                if not detail:
                    continue
                try:
                    turns = json.loads(detail) if isinstance(detail, str) else detail
                    if not isinstance(turns, list):
                        continue
                    for turn in turns:
                        role = turn.get("role")
                        content = turn.get("content", "")
                        if role in ("user", "assistant") and content:
                            messages.append(ChatMessage(role=role, content=content))
                except (json.JSONDecodeError, TypeError):
                    continue

            return messages

        except Exception as e:
            logger.warning(f"Failed to load history for workspace {self.workspace_id}: {e}")
            return []

    # ── stream ───────────────────────────────────────────────────

    async def stream(
        self,
        user_message: UserMessage | dict,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Stream typed events from the agentic loop.

        Supports two entry modes:

        1. **Fresh run** – ``user_message`` is a ``UserMessage`` (or plain
           dict with a ``message`` key).
        2. **Resumed run** – ``user_message`` is a dict with
           ``_resumed=True``.
        """
        self._reset_token_index()

        # ── resumed run (after tool approval) ────────────────────
        if isinstance(user_message, dict) and user_message.get("_resumed"):
            async for event in self._handle_resume(user_message):
                yield event
            return

        # ── fresh run ────────────────────────────────────────────
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=self.system_prompt),
        ]
        messages.extend(await self._load_history())
        messages.extend(self._prepare_messages(user_message))

        async for event in self._agentic_loop(messages):
            yield event
