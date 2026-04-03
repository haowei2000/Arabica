"""Prompt-calling strategy — tool descriptions in the system prompt.

The LLM receives tool information as part of the system prompt text and
outputs tool calls using ``<tool_call>`` XML markers.  This works with
any LLM, including those that don't support native function calling.
"""

from collections.abc import AsyncGenerator
import json
import logging
import re
from typing import Any
import uuid

from openai import AsyncOpenAI

from structure.frameworks.tool_calling.models import (
    ChatMessage,
    LLMResponse,
    ToolCallRequest,
)
from structure.frameworks.tool_calling.prompt_template import build_tools_system_prompt
from structure.frameworks.tool_calling.strategy import ToolCallingStrategy

logger = logging.getLogger(__name__)

# Primary: <tool_call>...</tool_call> blocks (the only accepted format).
_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>",
    re.DOTALL,
)

# Fallback: bare JSON objects that have both "name" and "arguments" keys but
# were not wrapped in XML tags.  Only used when the primary regex yields
# nothing, so this never fires for well-behaved output.
_BARE_TOOL_RE = re.compile(
    r'(?:```(?:json)?\s*)?\{[^{}]*"name"\s*:\s*"[^"]+"\s*,[^{}]*"arguments"\s*:\s*\{',
    re.DOTALL,
)


# ── JSON repair helpers ────────────────────────────────────────────────────


def _extract_balanced_object(s: str) -> str | None:
    """Return the first balanced ``{...}`` substring, or ``None``."""
    depth = 0
    start = None
    in_string = False
    escape = False
    for i, ch in enumerate(s):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if start is None:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                return s[start : i + 1]
    return None


def _fix_trailing_commas(s: str) -> str:
    """Remove trailing commas before ``}`` or ``]``."""
    return re.sub(r",\s*([}\]])", r"\1", s)


def _fix_python_literals(s: str) -> str:
    """Replace Python ``True`` / ``False`` / ``None`` with JSON equivalents."""
    s = re.sub(r"\bTrue\b", "true", s)
    s = re.sub(r"\bFalse\b", "false", s)
    s = re.sub(r"\bNone\b", "null", s)
    return s  # noqa: RET504


def _fix_control_chars_in_strings(s: str) -> str:
    """Escape literal control characters (newline, tab, CR) inside JSON strings."""
    result: list[str] = []
    in_string = False
    escape = False
    _CTRL = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for ch in s:
        if escape:
            result.append(ch)
            escape = False
        elif ch == "\\" and in_string:
            result.append(ch)
            escape = True
        elif ch == '"':
            in_string = not in_string
            result.append(ch)
        elif in_string and ch in _CTRL:
            result.append(_CTRL[ch])
        else:
            result.append(ch)
    return "".join(result)


def _unwrap_string_arguments(s: str) -> str:
    """If the ``arguments`` value is a JSON-encoded string, decode it to an object."""

    def _replacer(m: re.Match) -> str:
        inner = m.group(1)
        try:
            decoded: str = json.loads('"' + inner + '"')  # unescape escape sequences
            parsed = json.loads(decoded)
            return '"arguments": ' + json.dumps(parsed, ensure_ascii=False)
        except (json.JSONDecodeError, Exception):
            return m.group(0)

    return re.sub(r'"arguments"\s*:\s*"((?:[^"\\]|\\.)*)"', _replacer, s)


def _hoist_top_level_args(data: dict) -> dict:
    """If ``arguments`` is absent, promote extra top-level keys into it.

    Some models omit the ``arguments`` wrapper and write all parameters at
    the top level alongside ``name``.  E.g.::

        {"name": "foo", "query": "bar"}  →  {"name": "foo", "arguments": {"query": "bar"}}
    """
    if "arguments" in data:
        return data
    reserved = {"name", "id"}
    args = {k: v for k, v in data.items() if k not in reserved}
    if args:
        result = {k: v for k, v in data.items() if k in reserved}
        result["arguments"] = args
        return result
    return data


def _try_loads(s: str) -> dict | None:
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None


def _repair_json(raw: str) -> dict | None:
    """Try progressively stronger heuristic repairs for LLM JSON output.

    Pipeline (each step feeds the next):
    1.  Strip markdown code fences.
    2.  Extract the first balanced ``{...}`` block.
    3.  Try the candidate as-is.
    4.  Fix trailing commas.
    5.  Fix Python ``True``/``False``/``None`` literals.
    6.  Escape unescaped control characters inside strings.
    7.  Unwrap a JSON-encoded ``arguments`` string into an object.
    8.  Apply all fixes at once as a last resort.

    After a successful parse the result is normalised with
    ``_hoist_top_level_args`` to handle models that omit the ``arguments``
    wrapper.
    """
    # Step 1 – strip fences
    s = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s).strip()

    # Step 2 – extract balanced object (more reliable than rfind)
    candidate = _extract_balanced_object(s) or s

    # Steps 3-8 – ordered repair attempts
    attempts = [
        candidate,
        _fix_trailing_commas(candidate),
        _fix_python_literals(candidate),
        _fix_control_chars_in_strings(candidate),
        _unwrap_string_arguments(candidate),
        # combined
        _unwrap_string_arguments(
            _fix_control_chars_in_strings(
                _fix_python_literals(_fix_trailing_commas(candidate))
            )
        ),
    ]

    for attempt in attempts:
        result = _try_loads(attempt)
        if result is not None:
            return _hoist_top_level_args(result)

    return None


def _make_tool_call(data: dict) -> ToolCallRequest | None:
    """Build a ``ToolCallRequest`` from a parsed dict, or return ``None`` on error."""
    try:
        return ToolCallRequest(
            id=data.get("id", str(uuid.uuid4())),
            name=data["name"],
            arguments=data.get("arguments", {}),
        )
    except KeyError:
        logger.error("tool_call JSON missing 'name' field: %s", data)
        return None


def _extract_bare_tool_calls(text: str) -> list[ToolCallRequest]:
    """Last-resort fallback: extract tool calls that were output as bare JSON.

    The LLM is instructed to always use ``<tool_call>`` XML, but some models
    occasionally ignore this.  This function rescues those responses so they
    are not silently dropped.  It logs a warning so the pattern can be tracked.
    """
    calls: list[ToolCallRequest] = []
    pos = 0
    while pos < len(text):
        obj_str = _extract_balanced_object(text[pos:])
        if obj_str is None:
            break
        data = _try_loads(obj_str) or _repair_json(obj_str)
        if data and "name" in data and ("arguments" in data or len(data) > 2):
            data = _hoist_top_level_args(data)
            tc = _make_tool_call(data)
            if tc is not None:
                logger.warning(
                    "Tool call '%s' not wrapped in <tool_call> XML — recovered. "
                    "Check model output format.",
                    tc.name,
                )
                calls.append(tc)
        pos += text[pos:].find(obj_str) + len(obj_str)
        if pos <= 0:
            break
    return calls


def _parse_tool_calls_from_text(text: str) -> list[ToolCallRequest]:
    """Extract ``ToolCallRequest`` instances from LLM output text.

    Primary path: ``<tool_call>...</tool_call>`` XML blocks (the only format
    the model is instructed to use).

    Fallback path: if no XML blocks are found but the text contains what looks
    like bare JSON tool-call objects, rescue them and log a warning.
    """
    calls: list[ToolCallRequest] = []
    for match in _TOOL_CALL_RE.finditer(text):
        raw = match.group(1).strip()
        data = _try_loads(raw)
        if data is None:
            data = _repair_json(raw)
            if data is None:
                logger.error("Failed to parse tool_call block: %s", raw)
                continue
            logger.debug("Repaired malformed tool_call JSON (original: %.120s)", raw)
        else:
            data = _hoist_top_level_args(data)
        tc = _make_tool_call(data)
        if tc is not None:
            calls.append(tc)

    # Fallback: rescue bare JSON tool calls only when the XML path found nothing.
    if not calls and _BARE_TOOL_RE.search(text):
        calls = _extract_bare_tool_calls(text)

    return calls


def _strip_tool_call_blocks(text: str) -> str:
    """Remove ``<tool_call>...</tool_call>`` blocks from text."""
    return _TOOL_CALL_RE.sub("", text).strip()


class PromptCallingStrategy(ToolCallingStrategy):
    """Embeds tool descriptions in the system prompt and parses XML output."""

    # -- format_tools --------------------------------------------------------

    def format_tools(self, tool_classes: list[type]) -> str:
        """Return a text block describing all tools for the system prompt."""
        if not tool_classes:
            return ""
        return build_tools_system_prompt(tool_classes)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def build_client(api_key: str, base_url: str) -> AsyncOpenAI:
        """Create an AsyncOpenAI client. Call once and reuse to avoid overhead."""
        return AsyncOpenAI(api_key=api_key, base_url=base_url)

    @staticmethod
    def _build_client(api_key: str, base_url: str) -> AsyncOpenAI:
        return AsyncOpenAI(api_key=api_key, base_url=base_url)

    @staticmethod
    def _inject_tools_prompt(
        messages: list[ChatMessage], tools_prompt: str
    ) -> list[dict[str, Any]]:
        """Build API message dicts, injecting tools_prompt into the system message."""
        result: list[dict[str, Any]] = []
        system_injected = False
        for msg in messages:
            d = msg.to_openai_dict()
            if msg.role == "system" and not system_injected and tools_prompt:
                d["content"] = (d.get("content") or "") + "\n\n" + tools_prompt
                system_injected = True
            result.append(d)

        # If there was no system message, prepend one with the tools prompt.
        if not system_injected and tools_prompt:
            result.insert(0, {"role": "system", "content": tools_prompt})

        return result

    # -- call_llm (non-streaming) -------------------------------------------

    async def call_llm(
        self,
        messages: list[ChatMessage],
        tools_info: Any,
        *,
        model: str,
        api_key: str,
        base_url: str,
    ) -> LLMResponse:
        client = self._build_client(api_key, base_url)
        api_messages = self._inject_tools_prompt(messages, tools_info or "")

        response = await client.chat.completions.create(
            model=model, messages=api_messages
        )
        choice = response.choices[0]
        raw_content = choice.message.content or ""

        tool_calls = _parse_tool_calls_from_text(raw_content)
        clean_content = (
            _strip_tool_call_blocks(raw_content) if tool_calls else raw_content
        )

        usage = response.usage
        return LLMResponse(
            content=clean_content,
            tool_calls=tool_calls,
            raw=response,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )

    # -- call_llm_stream (streaming) ----------------------------------------

    async def call_llm_stream(
        self,
        messages: list[ChatMessage],
        tools_info: Any,
        *,
        model: str,
        api_key: str,
        base_url: str,
        client: AsyncOpenAI | None = None,
    ) -> AsyncGenerator[str | LLMResponse, None]:
        if client is None:
            client = self._build_client(api_key, base_url)
        api_messages = self._inject_tools_prompt(messages, tools_info or "")

        stream = await client.chat.completions.create(
            model=model,
            messages=api_messages,
            stream=True,
            stream_options={"include_usage": True},
        )

        full_content = ""
        # Buffer for detecting <tool_call> tags at the end of the stream.
        # We hold back text once we see a potential opening '<'.
        hold_buf = ""
        # Whether we are inside a tool_call block region.
        in_tool_block = False
        input_tokens: int = 0
        output_tokens: int = 0

        async for chunk in stream:
            # Usage arrives in the final chunk (choices may be empty).
            if chunk.usage:
                input_tokens = chunk.usage.prompt_tokens or 0
                output_tokens = chunk.usage.completion_tokens or 0

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            token = delta.content or ""
            if not token:
                continue

            full_content += token

            # Accumulate while we might be inside a tool_call tag.
            hold_buf += token

            if not in_tool_block:
                # Check if the buffer might be the start of a <tool_call> tag.
                tag_start = "<tool_call>"
                idx = hold_buf.find("<")
                if idx == -1:
                    # No '<' at all — safe to flush everything.
                    yield hold_buf
                    hold_buf = ""
                elif tag_start.startswith(hold_buf[idx:]):
                    # Partial match — keep buffering.
                    # Flush everything before the '<'.
                    if idx > 0:
                        yield hold_buf[:idx]
                        hold_buf = hold_buf[idx:]
                elif "<tool_call>" in hold_buf:
                    # Full opening tag found.
                    before, _, after = hold_buf.partition("<tool_call>")
                    if before:
                        yield before
                    hold_buf = "<tool_call>" + after
                    in_tool_block = True
                else:
                    # '<' was a false positive — flush everything.
                    yield hold_buf
                    hold_buf = ""
            else:
                # Inside a tool_call block, keep buffering until </tool_call>.
                if "</tool_call>" in hold_buf:
                    # The block is complete.  There might be more text after.
                    end_idx = hold_buf.find("</tool_call>") + len("</tool_call>")
                    remainder = hold_buf[end_idx:]
                    hold_buf = remainder
                    in_tool_block = False
                    # Don't yield the tool_call block itself — it's not user-facing text.
                    # Check if the remainder starts another tool_call block.
                    if "<tool_call>" in hold_buf:
                        before, _, after = hold_buf.partition("<tool_call>")
                        if before.strip():
                            yield before
                        hold_buf = "<tool_call>" + after
                        in_tool_block = True

        # Flush any remaining buffered text that is not a tool_call block.
        if hold_buf:
            # Check if there are tool_call blocks in the remaining buffer.
            remaining_clean = _strip_tool_call_blocks(hold_buf)
            if remaining_clean:
                yield remaining_clean

        # Parse tool calls from the full output.
        tool_calls = _parse_tool_calls_from_text(full_content)
        clean_content = (
            _strip_tool_call_blocks(full_content) if tool_calls else full_content
        )

        yield LLMResponse(
            content=clean_content,
            tool_calls=tool_calls,
            raw=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
