#!/usr/bin/env python3
"""Default Executor – conversation context + structured event stream.

Implements a manual agentic loop with tool calling through injected
abstractions (``ToolProvider`` / ``ToolCaller``).  The executor never
imports concrete tool registries or tool modules directly – all
dependencies are injected via config by the Worker (composition root).

Tool calling is delegated to ``PromptCallingStrategy`` (tool descriptions
in system prompt + XML-tagged output parsing).

Event-driven execution flow:
    USER_MESSAGE -> start fresh agentic loop
    USER_FEEDBACK -> continue with feedback as the user turns
    TOOL_RESULT -> accumulate a result, resume loop when all tools done
    TOOL_ERROR -> accumulate error, resume loop when all tools are done

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
from aiwen.models.events.event import Event
from aiwen.registries.core import register_executor
from aiwen.schemas.app import AppConfig
from aiwen.schemas.events.event_payloads import EventType, UserMessage
from aiwen.schemas.llm.chat_llm import ChatLLM

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """\
You are an intelligent AI assistant operating inside the Aiwen platform.

## Session

- workspace_id: {workspace_id}
- run_id: {run_id}

## Core Concepts

### Knowledge  (path prefix: `knowledge/`)
Curated reference content stored in the workspace context store —
documentation, facts, notes. Use `list_context` or `glance_context`
with path `knowledge` to discover what is available.

### Skill  (path prefix: `skills/`)
Reusable procedures, prompt templates, and HOW-TO guides defined by
the workspace owner. Read a skill with `read_context` to obtain step-
by-step instructions you should follow.

### Tool  (path prefix: `tools/`)
Descriptions of executable capabilities installed in the workspace.
Browse with `list_context(path="tools")` to see which tools are
enabled before invoking them.

### Task
A tracked unit of work within the current run. Use `create_task` to
record a goal, `update_task` to mark progress (status: pending →
in_progress → done), and `list_tasks` to review open items. Break
complex requests into subtasks using `parent_task_id`.

### Artifact
A persistent, versioned output you produce — generated text, code,
analysis, data. Use `create_artifact` to save any valuable result;
content is automatically uploaded to storage. Use `read_artifact` or
`list_artifacts` to retrieve previous outputs.

## Context Store Operations

All context operations require `workspace_id`. Common patterns:
- Discover resources : `list_context(path="knowledge")` or `glance_context`
- Read a resource    : `read_context(path="knowledge/topic_name")`
- Save progress notes: `create_context` / `update_context`
- Search             : `search_context(query="...")`
- Hierarchy view     : `tree_context(root="skills")`

Available operations:
  glance_context | read_context | list_context | tree_context
  glob_context   | search_context
  create_context | update_context | delete_context

## Working Approach

1. Start by checking available knowledge and skills relevant to the request.
2. Break complex tasks into subtasks using `create_task`.
3. Save significant outputs with `create_artifact`.
4. Think step-by-step before calling tools; prefer to batch related lookups.

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
        self.max_iterations: int = config.get("max_iterations", 10)

        # ── Dependency-injected abstractions ─────────────────────
        self.tool_provider: ToolProvider | None = config.get("tool_provider")
        self.tool_caller: ToolCaller | None = config.get("tool_caller")

        # ── LLM connection info ──────────────────────────────────
        self._api_key, self._base_url = self._resolve_llm_config()

        # ── Tool calling strategy ────────────────────────────────
        self.strategy = PromptCallingStrategy()

        # Use pre-computed tools_info from Worker cache when available
        # (avoids Pydantic schema generation for 150+ tools on every run).
        if "tools_info" in config:
            self.tools_info: Any = config["tools_info"]
        else:
            tool_classes = self._collect_tool_classes()
            self.tools_info = self.strategy.format_tools(tool_classes)

        # Cache the LLM client so it is not recreated for every LLM call
        # within the same run (saves connection overhead on multi-iteration loops).
        self._llm_client = self.strategy.build_client(self._api_key, self._base_url)

        self.workspace_id: str = config.get("workspace_id", "")
        self.run_id: str = config.get("run_id", "")
        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            workspace_id=self.workspace_id, run_id=self.run_id
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

    async def process_event(self, event: Event) -> AsyncGenerator[Event, None]:
        """Route the four supported event types to streaming handlers.

        WaitingForTool is caught here so it never propagates to the worker's
        handle_event, which would incorrectly mark the run as failed.
        The TOOL_CALL events emitted before the exception have already been
        yielded to the worker and will be published normally.
        """
        self._event_queue.clear()
        if not event.payload:
            raise ValueError("Event payload is empty")
        try:
            match event.event_type:
                case EventType.USER_MESSAGE:
                    self._reset_token_index()
                    async for e in self._on_user_message(event.payload):
                        yield e

                case EventType.USER_FEEDBACK:
                    self._reset_token_index()
                    async for e in self._on_user_feedback(event.payload):
                        yield e

                case EventType.TOOL_RESULT:
                    async for e in self._on_tool_result(event.payload):
                        yield e

                case EventType.TOOL_ERROR:
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
    ) -> AsyncGenerator[Event, None]:
        """Start a fresh agentic loop for a new user message."""
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=self.system_prompt),
        ]
        messages.extend(await self._load_history())
        messages.extend(await self._load_run_event_messages())

        # Inject pre-fetched workspace context from trigger processor
        trigger_ctx = self._extract_trigger_context(payload)
        if trigger_ctx:
            messages.append(self._build_trigger_context_message(trigger_ctx))

        messages.extend(self._prepare_messages(payload))

        async for event in self._agentic_loop(messages):
            yield event

    async def _on_user_feedback(
        self, payload: dict[str, Any]
    ) -> AsyncGenerator[Event, None]:
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
    ) -> AsyncGenerator[Event, None]:
        """Accumulate a successful tool result; call LLM when all tools done."""
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

        # All tools done — build messages and call LLM directly
        messages = self._waiting_messages or [
            ChatMessage(role="system", content=self.system_prompt)
        ]
        for r in self._tool_results:
            content = json.dumps(r["result"], ensure_ascii=False, default=str)
            messages.append(
                ChatMessage(role="tool", content=content, tool_call_id=r["tool_id"])
            )

        self._waiting_messages = None
        self._pending_tool_ids = set()
        self._tool_results = []

        self._reset_token_index()
        async for event in self._agentic_loop(messages):
            yield event

    async def _on_tool_error(
        self, payload: dict[str, Any]
    ) -> AsyncGenerator[Event, None]:
        """Accumulate a failed tool result; call LLM when all tools done."""
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

        # All tools done — build messages and call LLM directly
        messages = self._waiting_messages or [
            ChatMessage(role="system", content=self.system_prompt)
        ]
        for r in self._tool_results:
            content = json.dumps(
                {"success": False, "error": r.get("error_message", "Unknown error")},
                ensure_ascii=False,
            )
            messages.append(
                ChatMessage(role="tool", content=content, tool_call_id=r["tool_id"])
            )

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

    # ── run history loading ───────────────────────────────────────

    async def _load_run_history(self) -> ChatMessage | None:
        """Fetch the current run's event history from Redis via get_run_history.

        Uses the injected ``tool_caller`` so the executor stays decoupled from
        the tool registry.  Returns a system ``ChatMessage`` ready to be
        inserted into the conversation, or ``None`` if unavailable / empty.
        """
        if not self.run_id or not self.tool_caller:
            return None
        try:
            result = await self.tool_caller.call(
                "get_run_history",
                {"run_id": self.run_id, "limit": 50},
            )
            events = (result or {}).get("data", {}).get("events", [])
            if not events:
                return None

            lines = ["[Current run event history]"]
            for e in events:
                event_type = e.get("event_type", "")
                payload = e.get("payload") or {}
                seq = e.get("sequence", "?")

                if event_type == EventType.USER_MESSAGE:
                    msg = payload.get("message", "")
                    if isinstance(msg, list):
                        msg = " ".join(
                            p.get("text", "") if isinstance(p, dict) else str(p)
                            for p in msg
                        )
                    lines.append(f"[{seq}] User: {str(msg)[:300]}")

                elif event_type == EventType.AGENT_MESSAGE:
                    content = payload.get("content") or payload.get("message", "")
                    lines.append(f"[{seq}] Assistant: {str(content)[:300]}")

                elif event_type == EventType.TOOL_CALL:
                    tool_name = payload.get("tool_name", "?")
                    args = json.dumps(
                        payload.get("arguments", {}), ensure_ascii=False
                    )
                    lines.append(f"[{seq}] Tool call: {tool_name}({args[:200]})")

                elif event_type == EventType.TOOL_RESULT:
                    tool_name = payload.get("tool_name", "?")
                    res = payload.get("result")
                    res_str = (
                        json.dumps(res, ensure_ascii=False, default=str)[:300]
                        if res is not None
                        else "null"
                    )
                    lines.append(f"[{seq}] Tool result: {tool_name} → {res_str}")

                elif event_type == EventType.TOOL_ERROR:
                    tool_name = payload.get("tool_name", "?")
                    error = payload.get("error_message", "unknown error")
                    lines.append(f"[{seq}] Tool error: {tool_name} → {error[:200]}")

            if len(lines) <= 1:
                return None

            return ChatMessage(role="system", content="\n".join(lines))

        except Exception as e:
            logger.warning(f"Failed to load run history for run {self.run_id}: {e}")
            return None

    async def _load_run_event_messages(self) -> list[ChatMessage]:
        """Load prior conversation turns for this run from the Redis stream.

        Fetches via ``get_run_history`` (same source as ``_load_run_history``)
        so events that have not yet been persisted to PostgreSQL are included.
        Filters to only USER_MESSAGE / AGENT_MESSAGE events and converts them
        to proper role-based ChatMessages.

        The current user message (the most recent USER_MESSAGE, which triggered
        this handler) is excluded — it is appended separately by
        ``_prepare_messages``.  Returns an empty list when ``run_id`` or
        ``tool_caller`` is not available, or the stream has no prior turns.
        """
        if not self.run_id or not self.tool_caller:
            return []
        try:
            result = await self.tool_caller.call(
                "get_run_history",
                {
                    "run_id": self.run_id,
                    "limit": 200,
                    "exclude_types": [
                        EventType.AGENT_TOKEN,
                        EventType.AGENT_THINKING,
                        EventType.AGENT_HEARTBEAT,
                        EventType.TOOL_CALL,
                        EventType.TOOL_RESULT,
                        EventType.TOOL_ERROR,
                    ],
                },
            )
            raw_events = (result or {}).get("data", {}).get("events", [])
            if not raw_events:
                return []

            # Keep only conversation-turn events, in stream order.
            turn_types = {str(EventType.USER_MESSAGE), str(EventType.AGENT_MESSAGE)}
            events = [e for e in raw_events if e.get("event_type") in turn_types]

            # Remove the last USER_MESSAGE — it is the current trigger and will
            # be appended again by _prepare_messages.
            last_user_idx = -1
            for i, e in enumerate(events):
                if e.get("event_type") == str(EventType.USER_MESSAGE):
                    last_user_idx = i
            if last_user_idx >= 0:
                events = events[:last_user_idx]

            # Keep only the most recent max_history_messages events.
            if len(events) > self.max_history_messages:
                events = events[-self.max_history_messages :]

            messages: list[ChatMessage] = []
            for e in events:
                payload = e.get("payload") or {}
                event_type = e.get("event_type", "")
                if event_type == str(EventType.USER_MESSAGE):
                    msg = payload.get("message", "")
                    if isinstance(msg, list):
                        msg = " ".join(
                            p.get("text", "") if isinstance(p, dict) else str(p)
                            for p in msg
                        )
                    if msg:
                        messages.append(ChatMessage(role="user", content=str(msg)))
                elif event_type == str(EventType.AGENT_MESSAGE):
                    content = payload.get("content") or payload.get("message", "")
                    if content:
                        messages.append(
                            ChatMessage(role="assistant", content=str(content))
                        )
            return messages

        except Exception as e:
            logger.warning(
                f"Failed to load run event messages for run {self.run_id}: {e}"
            )
            return []

    # ── agentic loop (core streaming logic) ──────────────────────

    async def _agentic_loop(
        self,
        messages: list[ChatMessage],
    ) -> AsyncGenerator[Event, None]:
        """Run the agentic loop: call LLM, process tool calls, repeat."""
        # Reserve a slot at index 1 (right after system prompt) for run history.
        # It is inserted once here and refreshed in-place before every LLM call
        # so the model always sees the latest event stream.
        run_history_idx: int = -1
        initial_history = await self._load_run_history()
        if initial_history is not None:
            messages.insert(1, initial_history)
            run_history_idx = 1

        for _iteration in range(self.max_iterations):
            # ── refresh run history before each LLM call ─────────
            if run_history_idx >= 0:
                fresh = await self._load_run_history()
                if fresh is not None:
                    messages[run_history_idx] = fresh

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
                client=self._llm_client,
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
            else:
                async for e in self._emit_tool_calls(llm_response.tool_calls, messages):
                    yield e

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
                results = await service.descendants(f"{self.workspace_id}/long_memory")
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

    async def _emit_tool_calls(
        self,
        tool_calls: list[ToolCallRequest],
        messages: list[ChatMessage],
    ) -> AsyncGenerator[Event, None]:
        """Emit one TOOL_CALL event per tool, save state, then pause the loop.

        Saves the current message history so ``_resume_with_results`` can
        reconstruct the conversation when all tool results arrive.
        Raises ``WaitingForTool`` after yielding all events, which propagates
        up to ``process_event`` where it is caught and silenced.
        """
        self._waiting_messages = list(messages)

        for tc in tool_calls:
            self._pending_tool_ids.add(tc.id)
            yield self._emit_tool_call(tc.name, tc.id, tc.arguments)

        raise WaitingForTool(
            {
                "type": "tool_calls",
                "tool_calls": [
                    {"tool_name": tc.name, "tool_id": tc.id, "arguments": tc.arguments}
                    for tc in tool_calls
                ],
            }
        )
