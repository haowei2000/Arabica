"""Real LLM benchmark agent using an OpenAI-compatible chat API."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from openai import AsyncOpenAI

from benchmarks.baselines.memory_agents import (
    case_chunk_records,
    evidence_from_selected_context,
    select_lexical_chunks,
)
from benchmarks.core.types import BenchmarkCase, BenchmarkResult, CostLedger

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"


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
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0
    client: Any | None = None

    def __post_init__(self) -> None:
        if self.client is None:
            if not self.api_key:
                raise ValueError("LLMBenchmarkAgent requires an API key")
            self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    def _select_context(self, case: BenchmarkCase) -> list[tuple[str, str]]:
        chunks = case_chunk_records(case)
        if self.context_mode == "fulltext":
            return chunks
        if self.context_mode == "naiverag":
            question = str(case.inputs.get("question") or "")
            return select_lexical_chunks(question, chunks, top_k=self.top_k)
        raise ValueError(f"unsupported context_mode: {self.context_mode}")

    def _messages(
        self,
        case: BenchmarkCase,
        selected: list[tuple[str, str]],
    ) -> list[dict[str, str]]:
        context = "\n\n".join(
            f"[{chunk_id}]\n{text}" for chunk_id, text in selected
        )
        if len(context) > self.max_context_chars:
            context = context[-self.max_context_chars :]

        question = str(case.inputs.get("question") or "")
        wants_evidence = bool(
            isinstance(case.reference, dict) and "evidence_ids" in case.reference
        )
        answer_instruction = (
            "Return JSON with keys answer and evidence_ids. Use evidence_ids from "
            "the bracketed context ids when possible."
            if wants_evidence
            else "Return a concise answer, not an explanation."
        )
        return [
            {
                "role": "system",
                "content": (
                    "You are evaluating long-term conversational memory. "
                    "Answer the user's question using only the provided sessions. "
                    + answer_instruction
                ),
            },
            {
                "role": "user",
                "content": f"Sessions:\n{context}\n\nQuestion:\n{question}\n\nAnswer:",
            },
        ]

    async def run(self, case: BenchmarkCase) -> BenchmarkResult:
        selected = self._select_context(case)
        messages = self._messages(case, selected)
        started = time.monotonic()
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
        )
        latency = time.monotonic() - started
        content = _choice_content(response)
        usage = getattr(response, "usage", None)
        prompt_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
        completion_tokens = _usage_value(
            usage,
            "completion_tokens",
            "output_tokens",
        )
        usd_cost = (
            prompt_tokens * self.input_cost_per_mtok
            + completion_tokens * self.output_cost_per_mtok
        ) / 1_000_000
        return BenchmarkResult(
            task_id=case.task_id,
            response=content,
            cost=CostLedger(
                tokens_prompt=prompt_tokens,
                tokens_completion=completion_tokens,
                steps=1,
                latency_seconds=latency,
                usd_cost=usd_cost,
            ),
            evidence=evidence_from_selected_context(case, selected),
            metadata={
                "model": self.model,
                "base_url": self.base_url,
                "context_mode": self.context_mode,
                "selected_chunks": len(selected),
            },
        )
