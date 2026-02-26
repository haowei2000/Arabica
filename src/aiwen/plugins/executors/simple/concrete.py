#!/usr/bin/env python3
"""Default Executor – conversation context + structured event stream.

Implements a manual agentic loop with tool calling through injected
abstractions (``ToolProvider`` / ``ToolCaller``).  The executor never
imports concrete tool registries or tool modules directly – all
dependencies are injected via config by the Worker (composition root).

Tool calling is delegated to ``PromptCallingStrategy`` (tool descriptions
in system prompt + XML-tagged output parsing).

Event-driven execution flow:
    USER_MESSAGE     ->  start fresh agentic loop
    USER_FEEDBACK    ->  continue with feedback as user turn
    TOOL_RESULT      ->  accumulate result, resume loop when all tools done
    TOOL_ERROR       ->  accumulate error, resume loop when all tools done

Streaming event map:
    LLM streaming        ->  AGENT_THINKING  (inside <think> block)
                          ->  AGENT_TOKEN     (normal response text)
    LLM final response   ->  AGENT_MESSAGE   (no tool calls)
    Tool execution       ->  TOOL_CALL -> TOOL_RESULT / TOOL_ERROR
"""

from collections.abc import AsyncGenerator
import json
import logging
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
    LLMResponse,
    PromptCallingStrategy,
    ToolCallRequest,
)
from aiwen.registries.core import register_executor
from aiwen.schemas.app import AppConfig
from aiwen.schemas.events.event_payloads import EventType, UserMessage
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
    When the LLM response contains tool calls, TOOL_CALL events are emitted
    and the loop pauses.  Tool results arrive as TOOL_RESULT/TOOL_ERROR events
    and the loop resumes once all pending results are collected.

    Dependency Inversion:
      - ``ToolProvider``  – provides available tool classes (injected via config)
      - ``ToolCaller``    – executes a tool by name (injected via config)

    """

    TEMPLATE: ClassVar[dict[str, Any]] = {
        "executor_code": "DefaultExecutor",
        "executor_name": "DefaultExecutor",
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
        self.strategy = PromptCallingStrategy()

        # Collect tool classes and pre-format them for the strategy.
        tool_classes = self._collect_tool_classes()
        self.tools_info: Any = self.strategy.format_tools(tool_classes)

        self.workspace_id: str = config.get("workspace_id", "")
        run_id: str = config.get("run_id", "")
        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            workspace_id=self.workspace_id, run_id=run_id
        )

        # ── State for multi-tool tracking across events ──────────
        # Populated in _process_tool_calls before raising WaitingForTool;
        # cleared in _resume_with_results after all pending tools complete.
        self._waiting_messages: list[ChatMessage] | None = None
        self._pending_tool_ids: set[str] = set()
        self._tool_results: list[dict[str, Any]] = []

    # ── abstract method ───────────────────────────────────────────

    async def setup(self) -> None:
        """No external resources to set up for this executor."""

    # ── event dispatch ─────────────────────────────────────────────

    async def process_event(self, event: AgentEvent) -> AsyncGenerator[AgentEvent, None]:
        """Route the four supported event types to streaming handlers.

        WaitingForTool is caught here so it never propagates to the worker's
        handle_event, which would incorrectly mark the run as failed.
        The TOOL_CALL events emitted before the exception have already been
        yielded to the worker and will be published normally.
        """
        self._event_queue.clear()

        try:
            match event.event_type:
                case EventType.USER_MESSAGE.value:
                    self._reset_token_index()
                    async for e in self._on_user_message(event.payload):
                        yield e

                case EventType.USER_FEEDBACK.value:
                    self._reset_token_index()
                    async for e in self._on_user_feedback(event.payload):
                        yield e

                case EventType.TOOL_RESULT.value:
                    async for e in self._on_tool_result(event.payload):
                        yield e

                case EventType.TOOL_ERROR.value:
                    async for e in self._on_tool_error(event.payload):
                        yield e

                case _:
                    pass  # All other event types are not handled by this executor

        except WaitingForTool:
            # TOOL_CALL events were already yielded above; execution is now
            # paused.  The loop resumes when TOOL_RESULT/TOOL_ERROR events arrive.
            pass

    # ── streaming event handlers ──────────────────────────────────

    async def _on_user_message(
        self, payload: dict[str, Any]
    ) -> AsyncGenerator[AgentEvent, None]:
        """Start a fresh agentic loop for a new user message."""
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=self.system_prompt),
        ]
        messages.extend(await self._load_history())

        # Inject pre-fetched workspace context from trigger processor
        trigger_ctx = self._extract_trigger_context(payload)
        if trigger_ctx:
            messages.append(self._build_trigger_context_message(trigger_ctx))

        messages.extend(self._prepare_messages(payload))

        async for event in self._agentic_loop(messages):
            yield event

    async def _on_user_feedback(
        self, payload: dict[str, Any]
    ) -> AsyncGenerator[AgentEvent, None]:
        """Continue the conversation with user feedback as a new user turn."""
        feedback = payload.get("feedback", "")
        messages = [
            ChatMessage(role="system", content=self.system_prompt),
            ChatMessage(role="user", content=str(feedback)),
        ]
        async for event in self._agentic_loop(messages):
            yield event

    async def _on_tool_result(
        self, payload: dict[str, Any]
    ) -> AsyncGenerator[AgentEvent, None]:
        """Accumulate a successful tool result; resume the loop when all tools done."""
        tool_id = payload.get("tool_id", "")
        self._tool_results.append(
            {
                "tool_id": tool_id,
                "tool_name": payload.get("tool_name", ""),
                "result": payload.get("result", {}),
                "success": True,
            }
        )
        self._pending_tool_ids.discard(tool_id)

        if self._pending_tool_ids:
            return  # Still waiting for other tool results

        async for event in self._resume_with_results():
            yield event

    async def _on_tool_error(
        self, payload: dict[str, Any]
    ) -> AsyncGenerator[AgentEvent, None]:
        """Accumulate a failed tool result; resume the loop when all tools done."""
        tool_id = payload.get("tool_id", "")
        self._tool_results.append(
            {
                "tool_id": tool_id,
                "tool_name": payload.get("tool_name", ""),
                "result": None,
                "success": False,
                "error_message": payload.get("error_message", "Unknown error"),
            }
        )
        self._pending_tool_ids.discard(tool_id)

        if self._pending_tool_ids:
            return  # Still waiting for other tool results

        async for event in self._resume_with_results():
            yield event

    async def _resume_with_results(self) -> AsyncGenerator[AgentEvent, None]:
        """Append all collected tool results to the conversation and resume the loop."""
        messages = self._waiting_messages or []

        for r in self._tool_results:
            if r["success"]:
                content = json.dumps(r["result"], ensure_ascii=False, default=str)
            else:
                content = json.dumps(
                    {"success": False, "error": r.get("error_message", "Unknown error")},
                    ensure_ascii=False,
                )
            messages.append(
                ChatMessage(
                    role="tool",
                    content=content,
                    tool_call_id=r["tool_id"],
                )
            )

        # Clear pending state before resuming (loop may emit more tool calls)
        self._waiting_messages = None
        self._pending_tool_ids = set()
        self._tool_results = []

        self._reset_token_index()
        async for event in self._agentic_loop(messages):
            yield event

    # ── LLM config ────────────────────────────────────────────────

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

    # ── tool loading (via injected ToolProvider) ──────────────────

    def _collect_tool_classes(self) -> list[type]:
        """Collect tool classes from the injected ToolProvider."""
        if self.tool_provider is None:
            logger.warning("No ToolProvider injected; executor has no tools")
            return []

        classes: list[type] = []
        for tool_class in self.tool_provider.get_tool_classes():
            try:
                if not isinstance(tool_class, type):
                    tool_class = type(tool_class)
                classes.append(tool_class)
            except Exception as e:
                name = getattr(getattr(tool_class, "METADATA", None), "name", tool_class)
                logger.error(f"Failed to collect tool {name}: {e}", exc_info=True)

        logger.info(f"Collected {len(classes)} tool classes via ToolProvider")
        return classes

    # ── message serialization ──────────────────────────────────────

    @staticmethod
    def _serialize_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
        """Serialize the ChatMessage list to JSON-compatible format."""
        return [msg.to_dict() for msg in messages]

    @staticmethod
    def _deserialize_messages(serialized: list[dict[str, Any]]) -> list[ChatMessage]:
        """Reconstruct the ChatMessage list from a serialized format."""
        return [ChatMessage.from_dict(d) for d in serialized]

    # ── tool call processing ──────────────────────────────────────

    async def _process_tool_calls(
        self,
        tool_calls: list[ToolCallRequest] | list[dict[str, Any]],
        messages: list[ChatMessage],
    ) -> AsyncGenerator[AgentEvent, None]:
        """Emit TOOL_CALL events and pause execution for the worker to handle.

        1. Emits tool.call events for each tool call.
        2. Saves conversation state to instance variables for later resumption.
        3. Raises WaitingForTool – caught by process_event, not the worker.
        4. Worker processes TOOL_CALL events and publishes TOOL_RESULT/TOOL_ERROR.
        5. _on_tool_result/_on_tool_error accumulate results and resume the loop.
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

        # Emit tool.call events for all tools
        for tc in normalized:
            yield self._emit_tool_call(
                tool_name=tc.name,
                tool_id=tc.id,
                arguments=tc.arguments,
            )

        # Save conversation state so _resume_with_results can continue the loop
        self._waiting_messages = list(messages)
        self._pending_tool_ids = {tc.id for tc in normalized}
        self._tool_results = []

        # Pause the agentic loop; caught by process_event's try/except
        raise WaitingForTool(
            {
                "type": "tool_execution",
                "pending_tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in normalized
                ],
                "messages": self._serialize_messages(messages),
                "executor_code": self.TEMPLATE["executor_code"],
            }
        )

    # ── message conversion ────────────────────────────────────────

    def _extract_trigger_context(
        self, user_message: UserMessage | dict
    ) -> list[dict] | None:
        """Extract ``_trigger_context`` from a user message if present."""
        if isinstance(user_message, dict):
            return user_message.get("_trigger_context") or None
        extra = getattr(user_message, "model_extra", None) or {}
        value = extra.get("_trigger_context")
        return value if value else None

    def _build_trigger_context_message(
        self, trigger_context: list[dict]
    ) -> ChatMessage:
        """Build a system ChatMessage that injects workspace trigger results."""
        lines = ["[Workspace context retrieved by triggers]"]
        for item in trigger_context:
            name = item.get("trigger_name", "trigger")
            action = item.get("tool_name", "")
            result = item.get("result")
            lines.append(f"\n### {name} ({action})")
            if result is None:
                lines.append("(no result)")
            elif isinstance(result, (dict, list)):
                lines.append(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                lines.append(str(result))
        return ChatMessage(role="system", content="\n".join(lines))

    def _prepare_messages(self, user_message: UserMessage | dict) -> list[ChatMessage]:
        """Convert a UserMessage or payload dict to a ChatMessage list."""
        if isinstance(user_message, dict):
            message = user_message.get("message", "")
        else:
            message = user_message.message

        if isinstance(message, list):
            return [
                ChatMessage.from_dict(m) if isinstance(m, dict) else m
                for m in message
            ]

        if isinstance(message, str):
            return [ChatMessage(role="user", content=message)]

        return [ChatMessage(role="user", content=str(message))]

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
                    llm_response = item
                    continue

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

            # ── emit tool calls and pause (may raise WaitingForTool) ──
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

    # ── history loading ───────────────────────────────────────────

    async def _load_history(self) -> list[ChatMessage]:
        """Load previous conversation turns from WorkspaceContext."""
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
