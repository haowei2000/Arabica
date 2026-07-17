"""Real LLM benchmark agent using an OpenAI-compatible chat API."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from openai import AsyncOpenAI

from benchmarks.baselines.memory_agents import (
    approx_token_count,
    case_chunk_records,
    context_profile,
    evidence_from_selected_context,
    select_lexical_chunks,
)
from benchmarks.core.types import BenchmarkCase, BenchmarkResult, CostLedger


def _usage_value(usage: object, *names: str) -> int:
    for name in names:
        current = usage
        for part in name.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                current = getattr(current, part, None)
            if current is None:
                break
        if current is not None:
            return int(current or 0)
    return 0


def _choice_content(response: object) -> str:
    choices = getattr(response, "choices", None)
    if not choices and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        return ""
    first = choices[0]
    message = (
        first.get("message")
        if isinstance(first, dict)
        else getattr(first, "message", None)
    )
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


@dataclass
class LLMBenchmarkAgent:
    """Answer benchmark cases with a real chat model."""

    model: str = ""
    api_key: str = ""
    base_url: str = ""
    context_mode: str = "fulltext"
    top_k: int = 6
    temperature: float = 0.0
    max_context_chars: int = 120_000
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0
    client: Any | None = None

    def __post_init__(self) -> None:
        if self.client is None:
            if not self.api_key or not self.base_url or not self.model:
                raise ValueError(
                    "LLMBenchmarkAgent requires OPENAI__API_KEY, "
                    "OPENAI__BASE_URL, and OPENAI__MODEL"
                )
            self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    def _select_context(
        self,
        case: BenchmarkCase,
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        chunks = case_chunk_records(case)
        if self.context_mode == "fulltext":
            return chunks, chunks
        if self.context_mode == "naiverag":
            question = str(case.inputs.get("question") or "")
            return select_lexical_chunks(question, chunks, top_k=self.top_k), chunks
        if self.context_mode == "closedbook":
            # C0 contamination control (PROTOCOL.md section 2): the reader
            # answers from parametric knowledge alone. Chunks are still
            # reported as available for the diagnostics profile.
            return [], chunks
        raise ValueError(f"unsupported context_mode: {self.context_mode}")

    def _messages(
        self,
        case: BenchmarkCase,
        selected: list[tuple[str, str]],
    ) -> list[dict[str, str]]:
        context = "\n\n".join(f"[{chunk_id}]\n{text}" for chunk_id, text in selected)
        if len(context) > self.max_context_chars:
            context = context[-self.max_context_chars :]

        question = str(case.inputs.get("question") or "")
        wants_evidence = bool(
            isinstance(case.reference, dict) and "evidence_ids" in case.reference
        )
        if self.context_mode == "closedbook":
            # No context block and no mention of one: the arm measures what
            # the reader already knows. The JSON shape stays identical so the
            # evidence-aware scorers parse all arms the same way.
            answer_instruction = (
                "Return JSON with keys answer and evidence_ids; leave "
                "evidence_ids empty."
                if wants_evidence
                else "Return a concise answer, not an explanation."
            )
            return [
                {
                    "role": "system",
                    "content": (
                        "Answer the user's question from your own knowledge. "
                        "If you do not know, say you do not know. "
                        + answer_instruction
                    ),
                },
                {"role": "user", "content": f"Question:\n{question}\n\nAnswer:"},
            ]
        answer_instruction = (
            "Return JSON with keys answer and evidence_ids. Use evidence_ids from "
            "the bracketed context ids or evidence_id labels when possible."
            if wants_evidence
            else "Return a concise answer, not an explanation."
        )
        return [
            {
                "role": "system",
                "content": (
                    "You are evaluating long-term memory. "
                    "Answer the user's question using only the provided context. "
                    + answer_instruction
                ),
            },
            {
                "role": "user",
                "content": f"Context:\n{context}\n\nQuestion:\n{question}\n\nAnswer:",
            },
        ]

    async def run(self, case: BenchmarkCase) -> BenchmarkResult:
        selected, chunks = self._select_context(case)
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
        cached_tokens = _usage_value(
            usage,
            "prompt_tokens_details.cached_tokens",
            "cached_tokens",
            "prompt_cache_hit_tokens",
            "cache_read_input_tokens",
        )
        cache_creation_tokens = _usage_value(
            usage,
            "cache_creation_input_tokens",
            "prompt_cache_miss_tokens",
            "prompt_tokens_details.cache_creation_tokens",
        )
        cache_read_tokens = _usage_value(
            usage,
            "cache_read_input_tokens",
            "prompt_tokens_details.cached_tokens",
            "cached_tokens",
            "prompt_cache_hit_tokens",
        )
        usd_cost = (
            prompt_tokens * self.input_cost_per_mtok
            + completion_tokens * self.output_cost_per_mtok
        ) / 1_000_000
        context = "\n\n".join(f"[{chunk_id}]\n{text}" for chunk_id, text in selected)
        truncated = len(context) > self.max_context_chars
        prompt_chars = sum(len(message["content"]) for message in messages)
        return BenchmarkResult(
            task_id=case.task_id,
            response=content,
            cost=CostLedger(
                tokens_prompt=prompt_tokens,
                tokens_completion=completion_tokens,
                tokens_cached=cached_tokens,
                cache_creation_tokens=cache_creation_tokens,
                cache_read_tokens=cache_read_tokens,
                steps=1,
                latency_seconds=latency,
                usd_cost=usd_cost,
            ),
            evidence=evidence_from_selected_context(case, selected, response=content),
            metadata={
                "model": self.model,
                "base_url": self.base_url,
                "context_mode": self.context_mode,
                **context_profile(case, chunks, selected),
                "prompt_chars": prompt_chars,
                "prompt_tokens_approx": approx_token_count(
                    "\n".join(message["content"] for message in messages)
                ),
                "max_context_chars": self.max_context_chars,
                "context_truncated": truncated,
            },
        )
