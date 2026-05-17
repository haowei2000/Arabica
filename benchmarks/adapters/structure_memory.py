"""Structure-backed memory adapter for benchmark runs.

This adapter exercises the repository's file-based context service as the
memory store.  Each benchmark case gets an isolated synthetic workspace, chunks
are inserted as context entries, and query-time retrieval uses the same lexical
selector as the NaiveRAG smoke baseline before handing context to a fixed
OpenAI-compatible reader model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from benchmarks.baselines.llm_agent import LLMBenchmarkAgent
from benchmarks.baselines.memory_agents import (
    case_chunk_records,
    evidence_from_selected_context,
    select_lexical_chunks,
)
from benchmarks.core.types import BenchmarkCase, BenchmarkResult
from structure.services.context_service.manager import ContextManager
from structure.services.context_service.models import ContextCreateRequest


@dataclass
class StructureMemoryBenchmarkAgent:
    """Insert benchmark memory into Structure context and query it with a reader."""

    model: str
    api_key: str
    base_url: str
    data_root: str | Path = "benchmark_runs/structure-context"
    top_k: int = 6
    temperature: float = 0.0
    max_context_chars: int = 120_000
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0

    def __post_init__(self) -> None:
        self.manager = ContextManager(str(self.data_root))
        self.reader = LLMBenchmarkAgent(
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            context_mode="fulltext",
            temperature=self.temperature,
            max_context_chars=self.max_context_chars,
            input_cost_per_mtok=self.input_cost_per_mtok,
            output_cost_per_mtok=self.output_cost_per_mtok,
        )

    def _workspace_id(self, case: BenchmarkCase):
        return uuid5(NAMESPACE_URL, f"structure-benchmark:{case.task_id}")

    def _insert_case_context(self, case: BenchmarkCase) -> list[tuple[str, str]]:
        workspace_id = self._workspace_id(case)
        chunks = case_chunk_records(case)
        for index, (chunk_id, text) in enumerate(chunks, start=1):
            safe_id = chunk_id.replace("/", "_")
            self.manager.create_context(
                workspace_id,
                ContextCreateRequest(
                    path=f"benchmarks/{case.task_id}/chunks/{index:04d}-{safe_id}",
                    name=chunk_id,
                    content=text,
                    content_type="text/plain",
                    glance=text[:200],
                    tags=["benchmark", "memory"],
                    meta={
                        "benchmark_task_id": case.task_id,
                        "chunk_id": chunk_id,
                        "ability": case.ability,
                    },
                ),
            )
        return chunks

    async def run(self, case: BenchmarkCase) -> BenchmarkResult:
        chunks = self._insert_case_context(case)
        selected = select_lexical_chunks(
            str(case.inputs.get("question") or ""),
            chunks,
            top_k=self.top_k,
        )
        reader_case = BenchmarkCase(
            task_id=case.task_id,
            inputs={
                "question": case.inputs.get("question"),
                "sessions": [[{"role": chunk_id, "content": text}] for chunk_id, text in selected],
            },
            reference=case.reference,
            ability=case.ability,
            metadata=case.metadata,
        )
        result = await self.reader.run(reader_case)
        return BenchmarkResult(
            task_id=result.task_id,
            response=result.response,
            cost=result.cost,
            evidence=evidence_from_selected_context(case, selected),
            metadata={
                **result.metadata,
                "adapter": "StructureMemoryBenchmarkAgent",
                "selected_chunks": len(selected),
                "available_chunks": len(chunks),
                "context_data_root": str(self.data_root),
            },
        )
