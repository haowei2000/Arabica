"""LLM request budgeting and prefix-cache observability helpers."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from structure.frameworks.tool_calling.models import ChatMessage
from structure.services.llm.tokenizer import (
    TokenCountResult,
    TokenizerService,
    stable_hash,
    stable_json_dumps,
)

_DEFAULT_CONTEXT_WINDOW = int(os.getenv("LLM_DEFAULT_CONTEXT_WINDOW", "32768"))
_DEFAULT_RESERVED_OUTPUT_TOKENS = int(
    os.getenv("LLM_RESERVED_OUTPUT_TOKENS", "2048")
)

_MODEL_CONTEXT_WINDOWS = {
    "gpt-5": 400000,
    "gpt-4.1": 1047576,
    "gpt-4o": 128000,
    "gpt-4": 8192,
    "qwen-plus": 131072,
    "qwen-max": 32768,
    "qwen-turbo": 1000000,
    "qwen3": 131072,
    "qwen2.5": 131072,
    "llama": 32768,
}

_BOOTSTRAP_TOOL_ORDER = {
    "list_context": 0,
    "read_context": 1,
}
_RUNTIME_CONTEXT_PREFIX = "Runtime context for this request."


@dataclass(frozen=True)
class BudgetResult:
    """Prepared LLM request plus token/prefix metadata."""

    messages: list[ChatMessage]
    tools_info: Any
    estimated_input_tokens: int
    token_count_cache_hit: bool
    prompt_prefix_hash: str
    tools_hash: str
    trimmed_message_count: int
    trim_reason: str
    token_budget: int
    stable_prefix_tokens: int
    tokenizer_backend: str

    def context_breakdown(self) -> dict[str, Any]:
        """Return compact metadata suitable for event payloads/logs."""
        return {
            "estimated_input_tokens": self.estimated_input_tokens,
            "token_count_cache_hit": self.token_count_cache_hit,
            "prompt_prefix_hash": self.prompt_prefix_hash,
            "tools_hash": self.tools_hash,
            "trimmed_message_count": self.trimmed_message_count,
            "trim_reason": self.trim_reason,
            "token_budget": self.token_budget,
            "stable_prefix_tokens": self.stable_prefix_tokens,
            "tokenizer_backend": self.tokenizer_backend,
        }


class ContextBudgetManager:
    """Trim LLM requests to a model budget without breaking tool-call pairs."""

    def __init__(
        self,
        *,
        model: str,
        tokenizer: TokenizerService | None = None,
        context_window: int | None = None,
        reserved_output_tokens: int | None = None,
        max_input_tokens: int | None = None,
    ) -> None:
        self.model = model
        self.tokenizer = tokenizer or TokenizerService()
        self.context_window = context_window or resolve_model_context_window(model)
        self.reserved_output_tokens = (
            reserved_output_tokens
            if reserved_output_tokens is not None
            else _DEFAULT_RESERVED_OUTPUT_TOKENS
        )
        self.max_input_tokens = max_input_tokens or max(
            1024,
            self.context_window - self.reserved_output_tokens,
        )

    def prepare(
        self,
        messages: list[ChatMessage],
        tools_info: Any,
    ) -> BudgetResult:
        """Return a budgeted request and observability metadata."""
        stable_tools = stable_tools_info(tools_info)
        messages = _strip_empty_messages(messages)
        count = self.tokenizer.count_request(self.model, messages, stable_tools)
        trimmed_count = 0
        trim_reason = "within_budget"

        if count.tokens > self.max_input_tokens:
            messages, trimmed_count = self._trim_messages(messages, stable_tools)
            count = self.tokenizer.count_request(self.model, messages, stable_tools)
            trim_reason = (
                "dropped_old_messages"
                if count.tokens <= self.max_input_tokens
                else "over_budget_no_safe_trim"
            )
            if trimmed_count == 0 and count.tokens > self.max_input_tokens:
                trim_reason = "over_budget_no_safe_trim"

        prefix_messages = stable_prefix_messages(messages)
        prefix_count = self.tokenizer.count_request(
            self.model,
            prefix_messages,
            stable_tools,
        )

        return BudgetResult(
            messages=messages,
            tools_info=stable_tools,
            estimated_input_tokens=count.tokens,
            token_count_cache_hit=count.cache_hit,
            prompt_prefix_hash=prompt_prefix_hash(prefix_messages, stable_tools),
            tools_hash=tools_hash(stable_tools),
            trimmed_message_count=trimmed_count,
            trim_reason=trim_reason,
            token_budget=self.max_input_tokens,
            stable_prefix_tokens=prefix_count.tokens,
            tokenizer_backend=count.backend,
        )

    def _trim_messages(
        self,
        messages: list[ChatMessage],
        tools_info: Any,
    ) -> tuple[list[ChatMessage], int]:
        units = _message_units(messages)
        latest_user_idx = _latest_user_index(messages)
        kept = [True] * len(units)
        trimmed_messages = 0

        for unit_idx, unit in enumerate(units):
            if _is_protected_unit(unit, latest_user_idx):
                continue

            kept[unit_idx] = False
            trimmed_messages += len(unit)
            candidate = _flatten_units(units, kept)
            count = self.tokenizer.count_request(self.model, candidate, tools_info)
            if count.tokens <= self.max_input_tokens:
                return candidate, trimmed_messages

        return _flatten_units(units, kept), trimmed_messages


def resolve_model_context_window(model: str) -> int:
    """Resolve an approximate context window for budget defaults."""
    lower = (model or "").lower()
    for prefix, window in _MODEL_CONTEXT_WINDOWS.items():
        if lower.startswith(prefix):
            return window
    return _DEFAULT_CONTEXT_WINDOW


def stable_tools_info(tools_info: Any) -> Any:
    """Return tool schemas in a deterministic order and JSON shape."""
    if not isinstance(tools_info, list):
        return tools_info

    normalized = [_normalize_tool(tool) for tool in tools_info]
    return sorted(normalized, key=_tool_sort_key)


def tools_hash(tools_info: Any) -> str:
    if not tools_info:
        return stable_hash("")
    return stable_hash(tools_info if isinstance(tools_info, str) else stable_tools_info(tools_info))


def stable_prefix_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Return leading stable system messages, excluding dynamic runtime IDs."""
    result: list[ChatMessage] = []
    for message in messages:
        if message.role != "system":
            break
        if message.content.startswith(_RUNTIME_CONTEXT_PREFIX):
            continue
        result.append(message)
    return result


def prompt_prefix_hash(messages: list[ChatMessage], tools_info: Any) -> str:
    payload = {
        "messages": [message.to_openai_dict() for message in messages],
        "tools": stable_tools_info(tools_info),
    }
    return stable_hash(payload)


def _normalize_tool(tool: Any) -> Any:
    if hasattr(tool, "model_dump"):
        tool = tool.model_dump(exclude_none=True)
    if isinstance(tool, dict):
        return _json_round_trip(tool)
    return tool


def _json_round_trip(value: Any) -> Any:
    return __import__("json").loads(stable_json_dumps(value))


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        function = tool.get("function")
        if isinstance(function, dict):
            return str(function.get("name") or "")
    function = getattr(tool, "function", None)
    return str(getattr(function, "name", "") or "")


def _tool_sort_key(tool: Any) -> tuple[int, str]:
    name = _tool_name(tool)
    return (_BOOTSTRAP_TOOL_ORDER.get(name, 100), name)


def _strip_empty_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    return [
        message
        for message in messages
        if message.content or message.tool_calls or message.role == "assistant"
    ]


def _latest_user_index(messages: list[ChatMessage]) -> int:
    for idx in range(len(messages) - 1, -1, -1):
        if messages[idx].role == "user":
            return idx
    return len(messages)


def _message_units(messages: list[ChatMessage]) -> list[list[tuple[int, ChatMessage]]]:
    units: list[list[tuple[int, ChatMessage]]] = []
    idx = 0
    while idx < len(messages):
        message = messages[idx]
        unit = [(idx, message)]
        if message.role == "assistant" and message.tool_calls:
            expected_ids = {tool_call.id for tool_call in message.tool_calls}
            seen_ids: set[str] = set()
            next_idx = idx + 1
            while next_idx < len(messages) and messages[next_idx].role == "tool":
                tool_message = messages[next_idx]
                unit.append((next_idx, tool_message))
                if tool_message.tool_call_id:
                    seen_ids.add(tool_message.tool_call_id)
                next_idx += 1
                if expected_ids and expected_ids.issubset(seen_ids):
                    break
            idx = next_idx
        else:
            idx += 1
        units.append(unit)
    return units


def _is_protected_unit(
    unit: list[tuple[int, ChatMessage]],
    latest_user_idx: int,
) -> bool:
    if any(message.role == "system" for _, message in unit):
        return True
    return any(idx >= latest_user_idx for idx, _ in unit)


def _flatten_units(
    units: list[list[tuple[int, ChatMessage]]],
    kept: list[bool],
) -> list[ChatMessage]:
    result: list[ChatMessage] = []
    for unit, keep in zip(units, kept, strict=True):
        if keep:
            result.extend(message for _, message in unit)
    return result
