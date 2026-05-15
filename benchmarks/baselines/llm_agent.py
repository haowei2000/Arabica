"""Real LLM benchmark agent using an OpenAI-compatible chat API."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from openai import AsyncOpenAI

from benchmarks.baselines.memory_agents import _select_lexical_chunks
from benchmarks.core.types import BenchmarkCase, BenchmarkResult, CostLedger

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"


def _turn_content(turn: object) -> str:
    if isinstance(turn, dict):
        role = turn.get("role") or turn.get("speaker") or "unknown"
        content = turn.get("content") or turn.get("text") or turn.get("utterance") or ""
        return f"{role}: {content}"
    return str(turn)


def _case_chunks(case: BenchmarkCase) -> list[str]:
    chunks: list[str] = []
    for session_index, session in enumerate(case.inputs.get("sessions") or [], start=1):
        if not isinstance(session, list):
            continue
        turns = [_turn_content(turn) for turn in session if turn]
        if turns:
            chunks.append(f"[Session {session_index}]\n" + "\n".join(turns))
    return chunks


def _usage_value(usage: object, *names: str) -> int:
    for name in names:
        if isinstance(usage, dict) and name in usage:
            return int(usage[name] or 0)
        value = getattr(usage, name, None)
        if value is not None:
            return int(value)
    return 0


def _choice_content(response: object) -> str:
    choices = getattr(response, "choices", None)
    if not choices and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        return ""
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


@dataclass
class LLMBenchmarkAgent:
    """Answer benchmark cases with a real chat model."""

    model: str = DEFAULT_MODEL
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    context_mode: str = "fulltext"
    top_k: int = 6
    temperature: float = 0.0
    max_context_chars: int = 120_000
    client: Any | None = None

    def __post_init__(self) -> None:
        if self.client is None:
            if not self.api_key:
                raise ValueError("LLMBenchmarkAgent requires an API key")
            self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    def _select_context(self, case: BenchmarkCase) -> list[str]:
        chunks = _case_chunks(case)
        if self.context_mode == "fulltext":
            return chunks
        if self.context_mode == "naiverag":
            question = str(case.inputs.get("question") or "")
            return _select_lexical_chunks(question, chunks, top_k=self.top_k)
        raise ValueError(f"unsupported context_mode: {self.context_mode}")

    def _messages(self, case: BenchmarkCase) -> list[dict[str, str]]:
        selected = self._select_context(case)
        context = "\n\n".join(selected)
        if len(context) > self.max_context_chars:
            context = context[-self.max_context_chars :]

        question = str(case.inputs.get("question") or "")
        return [
            {
                "role": "system",
                "content": (
                    "You are evaluating long-term conversational memory. "
                    "Answer the user's question using only the provided sessions. "
                    "Return a concise answer, not an explanation."
                ),
            },
            {
                "role": "user",
                "content": f"Sessions:\n{context}\n\nQuestion:\n{question}\n\nAnswer:",
            },
        ]

    async def run(self, case: BenchmarkCase) -> BenchmarkResult:
        messages = self._messages(case)
        started = time.monotonic()
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
        )
        latency = time.monotonic() - started
        content = _choice_content(response)
        usage = getattr(response, "usage", None)
        return BenchmarkResult(
            task_id=case.task_id,
            response=content,
            cost=CostLedger(
                tokens_prompt=_usage_value(usage, "prompt_tokens", "input_tokens"),
                tokens_completion=_usage_value(
                    usage,
                    "completion_tokens",
                    "output_tokens",
                ),
                steps=1,
                latency_seconds=latency,
            ),
            metadata={
                "model": self.model,
                "base_url": self.base_url,
                "context_mode": self.context_mode,
            },
        )

