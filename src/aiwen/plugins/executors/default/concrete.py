#!/usr/bin/env python3
"""Default Executor – conversation context + structured event stream.

Implements a manual agentic loop with tool calling through injected
abstractions (``ToolProvider`` / ``ToolCaller``).  The executor never
imports concrete tool registries or tool modules directly – all
dependencies are injected via config by the Worker (composition root).

Tool calling is delegated to ``PromptCallingStrategy`` (tool descriptions
in system prompt + XML-tagged output parsing).

Event-driven execution flow:
    USER_MESSAGE -> load full event history, start agentic loop
    USER_FEEDBACK -> continue with feedback as the user turn
    TOOL_RESULT -> load full event history (includes results), resume loop
    TOOL_ERROR  -> load full event history (includes errors), resume loop

Trigger context (set by event worker in payload["_context"]) is embedded
naturally when USER_MESSAGE events are converted to ChatMessages inside
_load_event_history — no trigger-specific logic in this module.

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
from aiwen.schemas.events.event_payloads import EventType
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
        self.global_event: bool = config.get("global_event", False)
        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            workspace_id=self.workspace_id, run_id=self.run_id
        )

        # Tracks tool IDs that have been emitted but not yet resolved.
        # Used to detect when all parallel tool calls are complete before
        # resuming the agentic loop.
        self._pending_tool_ids: set[str] = set()

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
                    async for e in self._on_user_message():
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

    async def _on_user_message(self) -> AsyncGenerator[Event, None]:
        """Start a fresh agentic loop for a new user message.

        Full conversation is reconstructed from the event stream via
        _load_event_history so the executor is stateless across events.
        Trigger context injected by the event worker (payload["_context"])
        is embedded naturally when the USER_MESSAGE event is converted.
        """
        messages = [ChatMessage(role="system", content=self.system_prompt)]
        messages.extend(await self._load_event_history())
        async for event in self._agentic_loop(messages):
            yield event

    async def _on_user_feedback(
        self, payload: dict[str, Any]
    ) -> AsyncGenerator[Event, None]:
        """Continue the conversation with user feedback as a new user turn."""
        feedback = payload.get("feedback", "")
        messages = [ChatMessage(role="system", content=self.system_prompt)]
        messages.extend(await self._load_event_history())
        messages.append(ChatMessage(role="user", content=str(feedback)))
        async for event in self._agentic_loop(messages):
            yield event

    async def _on_tool_result(
        self, payload: dict[str, Any]
    ) -> AsyncGenerator[Event, None]:
        """Discard a resolved tool ID; resume the loop when all tools are done.

        The tool result is already persisted in the event stream.
        _load_event_history will pick it up and include it in the
        reconstructed conversation — no in-memory accumulation needed.
        """
        self._pending_tool_ids.discard(payload.get("tool_id", ""))
        if self._pending_tool_ids:
            return  # Still waiting for other tool results

        messages = [ChatMessage(role="system", content=self.system_prompt)]
        messages.extend(await self._load_event_history())
        # TODO add the tool result
        messages.append(ChatMessage(role="assistant", content=payload.get("result", "")))
        self._reset_token_index()
        async for event in self._agentic_loop(messages):
            yield event

    async def _on_tool_error(
        self, payload: dict[str, Any]
    ) -> AsyncGenerator[Event, None]:
        """Discard a failed tool ID; resume the loop when all tools are done.

        Same stateless approach as _on_tool_result: the error event is in
        the stream and _load_event_history includes it automatically.
        """
        self._pending_tool_ids.discard(payload.get("tool_id", ""))
        if self._pending_tool_ids:
            return  # Still waiting for other tool results

        messages = [ChatMessage(role="system", content=self.system_prompt)]
        messages.extend(await self._load_event_history())
        messages.append(ChatMessage(role="assistant", content=payload.get("error", "")))
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

    async def _load_event_history(self) -> list[ChatMessage]:
        """Load the current run's event stream and convert to LLM-ready ChatMessages.

        Fetches USER_MESSAGE, AGENT_MESSAGE, TOOL_RESULT, and TOOL_ERROR events
        in sequence order and maps them to role-based ChatMessages compatible with
        PromptCallingStrategy (XML-based tool calling, no native tool_calls):

          USER_MESSAGE  -> system message for ``_context`` (if set by event worker)
                           followed by a user message
          AGENT_MESSAGE -> assistant message
          TOOL_RESULT   -> appended to the preceding assistant message content
          TOOL_ERROR    -> appended to the preceding assistant message content
          TOOL_CALL     -> skipped (content is embedded in the AGENT_MESSAGE text)

        Tool results are merged into the assistant turn rather than sent as
        role="tool" messages.  role="tool" requires a preceding assistant message
        with native tool_calls, which PromptCallingStrategy never produces.

        This is the single place where trigger context enters the LLM conversation:
        the event worker stores formatted context in payload["_context"] of the
        USER_MESSAGE event; this method reads it and injects it as a system message
        immediately before the user turn.
        """
        if not self.tool_caller:
            return []
        try:
            if self.global_event and self.workspace_id:
                # Load important events from all runs in the workspace so the
                # agent has cross-run context awareness.
                result = await self.tool_caller.call(
                    "get_workspace_history",
                    {"workspace_id": self.workspace_id, "limit": self.max_history_messages},
                )
            else:
                if not self.run_id:
                    return []
                result = await self.tool_caller.call(
                    "get_run_history",
                    {"run_id": self.run_id, "limit": 200},
                )
            raw_events = (result or {}).get("data", {}).get("events", [])
            if not raw_events:
                return []

            messages: list[ChatMessage] = []
            for e in raw_events:
                event_type = e.get("event_type", "")
                payload = e.get("payload") or {}

                if event_type == str(EventType.USER_MESSAGE):
                    # Inject trigger context pre-formatted by the event worker.
                    ctx = payload.get("_context")
                    if ctx:
                        messages.append(ChatMessage(role="system", content=str(ctx)))
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
                        messages.append(ChatMessage(role="assistant", content=str(content)))

                elif event_type == str(EventType.TOOL_RESULT):
                    tool_name = payload.get("tool_name", "")
                    result_data = payload.get("result")
                    result_str = json.dumps(result_data, ensure_ascii=False, default=str)
                    tool_text = f"\nTool result ({tool_name}):\n{result_str}"
                    # Append to the last assistant message so the model sees the
                    # result as part of its own reasoning turn (no role="tool" needed).
                    for i in range(len(messages) - 1, -1, -1):
                        if messages[i].role == "assistant":
                            messages[i].content += tool_text
                            break
                    else:
                        messages.append(ChatMessage(role="user", content=tool_text))

                elif event_type == str(EventType.TOOL_ERROR):
                    tool_name = payload.get("tool_name", "")
                    error = payload.get("error_message", "Unknown error")
                    tool_text = f"\nTool error ({tool_name}): {error}"
                    for i in range(len(messages) - 1, -1, -1):
                        if messages[i].role == "assistant":
                            messages[i].content += tool_text
                            break
                    else:
                        messages.append(ChatMessage(role="user", content=tool_text))

                # TOOL_CALL: skip — content is embedded in AGENT_MESSAGE text.

            return messages

        except Exception as e:
            logger.warning(f"Failed to load event history for run {self.run_id}: {e}")
            return []

    # ── agentic loop (core streaming logic) ──────────────────────

    async def _agentic_loop(
        self,
        messages: list[ChatMessage],
    ) -> AsyncGenerator[Event, None]:
        """Run the agentic loop: call LLM, process tool calls, repeat.

        ``messages`` is the complete conversation built by the caller
        (system prompt + long-term history + event history).  No additional
        history loading happens inside the loop; the caller is responsible
        for passing an up-to-date message list.
        """
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
                async for e in self._emit_tool_calls(llm_response.tool_calls):
                    yield e

            # Reset token index for the next LLM call iteration
            self._reset_token_index()

        else:
            yield self._emit_token("", is_final=True)
            yield self._emit_message(
                "Maximum tool-calling iterations reached. Stopping."
            )

    async def _emit_tool_calls(
        self,
        tool_calls: list[ToolCallRequest],
    ) -> AsyncGenerator[Event, None]:
        """Emit one TOOL_CALL event per tool, register pending IDs, then pause.

        Raises ``WaitingForTool`` after yielding all events so the worker
        knows the loop is suspended until TOOL_RESULT/TOOL_ERROR events arrive.
        """
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
