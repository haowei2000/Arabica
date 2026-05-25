from pathlib import Path
from types import SimpleNamespace

from benchmarks.adapters import (
    StructureMemoryBenchmarkAgent,
    StructurePathMemoryBenchmarkAgent,
)
from benchmarks.core import BenchmarkCase
from benchmarks.scripts.run_full_memory_benchmark import _make_agent, build_parser
import pytest


def _agent(tmp_path: Path, *, top_k: int = 1) -> StructurePathMemoryBenchmarkAgent:
    return StructurePathMemoryBenchmarkAgent(
        model="fake-model",
        api_key="test-key",
        base_url="http://example.test/v1",
        data_root=tmp_path,
        top_k=top_k,
    )


class _FakeCompletions:
    def __init__(self) -> None:
        self.messages = None

    async def create(self, **kwargs):
        self.messages = kwargs["messages"]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Berlin"))],
            usage=SimpleNamespace(
                prompt_tokens=123,
                completion_tokens=7,
                prompt_tokens_details=SimpleNamespace(cached_tokens=11),
            ),
        )


class _FakeClient:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


def _structure_agent(
    tmp_path: Path, *, top_k: int = 1
) -> StructureMemoryBenchmarkAgent:
    agent = StructureMemoryBenchmarkAgent(
        model="fake-model",
        api_key="test-key",
        base_url="http://example.test/v1",
        data_root=tmp_path,
        top_k=top_k,
    )
    agent.reader.client = _FakeClient()
    return agent


def _case(*, question: str = "When did the user move?") -> BenchmarkCase:
    return BenchmarkCase(
        task_id="path-time-case",
        inputs={
            "question": question,
            "sessions": [
                [
                    {"role": "metadata", "content": "session date: 1 May 2024"},
                    {"role": "user", "content": "The user lived in Lisbon."},
                ],
                [
                    {"role": "metadata", "content": "session date: 5 June 2024"},
                    {"role": "user", "content": "The user moved to Berlin."},
                ],
            ],
        },
        reference="Berlin",
        ability="temporal-reasoning",
        metadata={"evidence": ["D2:1"]},
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_structure_memory_uses_context_read_events_batches_and_run_tokens(
    tmp_path,
):
    agent = _structure_agent(tmp_path)
    case = _case(question="What changed in Berlin after the move?")

    result = await agent.run(case)

    assert result.response == "Berlin"
    assert result.cost.tokens_prompt == 123
    assert result.cost.tokens_completion == 7
    assert result.cost.tokens_cached == 11
    assert result.cost.tool_calls == 1
    assert result.metadata["token_source"] == "run_event_aggregate"
    assert result.metadata["retrieval_source"] == "context_store_read_context_events"
    assert result.metadata["batch_count"] >= 2
    assert result.metadata["gc_strategy"] == "event_count_ttl"
    assert result.metadata["selected_context_paths"] == [
        "benchmarks/path-time-case/chunks/0002-D2"
    ]

    messages = agent.reader.client.completions.messages
    assert any(message["role"] == "tool" for message in messages)
    assert "The user moved to Berlin." in "\n".join(
        str(message.get("content") or "") for message in messages
    )


@pytest.mark.unit
def test_structure_path_memory_writes_context_metadata(tmp_path):
    agent = _agent(tmp_path)
    case = _case()

    agent._insert_case_context(case)
    contexts = agent.manager.list_contexts(
        agent._workspace_id(case),
        prefix=f"benchmarks/{case.task_id}/chunks",
        recursive=True,
    )

    assert len(contexts) == 2
    by_chunk = {context.meta["chunk_id"]: context for context in contexts}
    assert by_chunk["D1"].meta["session_index"] == 1
    assert by_chunk["D1"].meta["session_date_text"] == "1 May 2024"
    assert by_chunk["D2"].meta["session_index"] == 2
    assert (
        by_chunk["D2"]
        .meta["path"]
        .startswith(f"benchmarks/{case.task_id}/chunks/0002-")
    )
    assert by_chunk["D2"].meta["ability"] == "temporal-reasoning"


@pytest.mark.unit
def test_structure_path_memory_prefers_matching_temporal_context(tmp_path):
    agent = _agent(tmp_path)
    case = _case(question="What changed on 5 June 2024?")

    agent._insert_case_context(case)
    selected, chunks = agent.select_contexts_from_store(case)

    assert len(chunks) == 2
    assert selected == [("D2", "session date: 5 June 2024\nThe user moved to Berlin.")]


@pytest.mark.unit
def test_structure_path_memory_uses_recency_for_current_questions(tmp_path):
    agent = _agent(tmp_path)
    case = _case(question="What is the user's current city?")

    agent._insert_case_context(case)
    selected, _ = agent.select_contexts_from_store(case)

    assert selected[0][0] == "D2"


@pytest.mark.unit
def test_structure_path_memory_retrieval_does_not_use_gold_fields(tmp_path):
    agent = _agent(tmp_path)
    base = _case(question="Where is the user now?")
    no_gold = BenchmarkCase(
        task_id=base.task_id,
        inputs=base.inputs,
        reference="a different answer",
        ability=base.ability,
        metadata={"evidence": ["D1:1"]},
    )

    agent._insert_case_context(base)
    selected_with_gold, _ = agent.select_contexts_from_store(base)
    selected_without_gold, _ = agent.select_contexts_from_store(no_gold)

    assert selected_with_gold == selected_without_gold


@pytest.mark.unit
def test_full_memory_runner_accepts_structure_path_memory_method(tmp_path):
    args = build_parser().parse_args(
        ["--benchmark", "locomo", "--methods", "StructurePathMemory"]
    )
    assert args.methods == ["StructurePathMemory"]

    agent = _make_agent(
        method="StructurePathMemory",
        api_key="test-key",
        base_url="http://example.test/v1",
        model="fake-model",
        top_k=1,
        temperature=0.0,
        max_context_chars=120_000,
        input_cost_per_mtok=0.0,
        output_cost_per_mtok=0.0,
        output_dir=tmp_path,
    )
    assert isinstance(agent, StructurePathMemoryBenchmarkAgent)
