"""Structure-backed memory adapter for benchmark runs.

This adapter exercises the repository's file-based context service as the
memory store.  Each benchmark case gets an isolated synthetic workspace, chunks
are inserted as context entries, and query-time retrieval uses the same lexical
selector as the NaiveRAG smoke baseline before handing context to a fixed
OpenAI-compatible reader model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import time
from typing import Any
import unicodedata
from uuid import NAMESPACE_URL, UUID, uuid5

from benchmarks.baselines.llm_agent import LLMBenchmarkAgent
from benchmarks.baselines.memory_agents import (
    approx_token_count,
    case_chunk_records,
    context_profile,
    evidence_from_selected_context,
    select_lexical_chunks,
)
from benchmarks.core.types import BenchmarkCase, BenchmarkResult, CostLedger
from structure.core.enums.events import ContextBatchLoadState, ContextBatchState
from structure.core.enums.runs import RunStatus, TriggerType
from structure.models.events.event import Event
from structure.models.events.event_batch import EventBatch
from structure.models.runs.run import Run
from structure.plugins.executors.default.concrete import DefaultExecutor
from structure.plugins.tools.context.read_context import ReadContextTool
from structure.schemas.events.event_payloads import EventType
from structure.services.context_service.manager import ContextManager
from structure.services.context_service.models import ContextCreateRequest
from structure.services.events.context_batch_service import ContextBatchService
from structure.services.events.event_archive import EventArchiveService
from structure.services.events.event_gc import DEFAULT_EVENT_GC_STRATEGY

_WORDY_PUNCT = re.compile(r"[^0-9a-z一-鿿\s]+")
_WHITESPACE = re.compile(r"\s+")
_DATE_FRAGMENT = re.compile(
    r"\b(?:\d{1,2}:\d{2}\s*(?:am|pm)?|\d{1,4}|"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"\b",
    re.IGNORECASE,
)
_TEMPORAL_CUES = {
    "when",
    "latest",
    "recent",
    "current",
    "currently",
    "before",
    "after",
    "now",
    "date",
    "time",
    "last",
    "newest",
    "最近",
    "最新",
    "当前",
    "现在",
    "之前",
    "之后",
    "什么时候",
    "日期",
    "时间",
}
_RECENCY_CUES = {
    "latest",
    "recent",
    "current",
    "currently",
    "now",
    "last",
    "newest",
    "最近",
    "最新",
    "当前",
    "现在",
}


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = _WORDY_PUNCT.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def _tokens(text: str) -> set[str]:
    return {token for token in _normalise(text).split() if len(token) > 2}


def _date_tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in _DATE_FRAGMENT.finditer(text)}


def _session_date_text(session: object) -> str | None:
    if not isinstance(session, list):
        return None
    for turn in session:
        if not isinstance(turn, dict):
            continue
        content = str(turn.get("content") or turn.get("text") or "")
        lowered = content.lower()
        if lowered.startswith("session date:"):
            return content.split(":", maxsplit=1)[1].strip()
    return None


def _chunk_metadata(case: BenchmarkCase, chunk_id: str, index: int) -> dict[str, Any]:
    sessions = case.inputs.get("sessions") or []
    session_index = index
    session_date = None
    if isinstance(sessions, list) and 0 <= index - 1 < len(sessions):
        session_date = _session_date_text(sessions[index - 1])
    return {
        "benchmark_task_id": case.task_id,
        "chunk_id": chunk_id,
        "session_index": session_index,
        "session_date_text": session_date,
        "ability": case.ability,
    }


def _safe_chunk_id(chunk_id: str) -> str:
    return chunk_id.replace("/", "_")


@dataclass
class StructureMemoryBenchmarkAgent:
    """Run memory benchmarks through Structure context, event, and batch paths."""

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

    def _run_id(self, case: BenchmarkCase) -> UUID:
        return uuid5(NAMESPACE_URL, f"structure-benchmark-run:{case.task_id}")

    def _user_id(self, case: BenchmarkCase) -> UUID:
        return uuid5(NAMESPACE_URL, f"structure-benchmark-user:{case.task_id}")

    def _insert_case_context(self, case: BenchmarkCase) -> list[tuple[str, str]]:
        workspace_id = self._workspace_id(case)
        chunks = case_chunk_records(case)
        for index, (chunk_id, text) in enumerate(chunks, start=1):
            safe_id = _safe_chunk_id(chunk_id)
            path = f"benchmarks/{case.task_id}/chunks/{index:04d}-{safe_id}"
            meta = {**_chunk_metadata(case, chunk_id, index), "path": path}
            self.manager.create_context(
                workspace_id,
                ContextCreateRequest(
                    path=path,
                    name=chunk_id,
                    content=text,
                    content_type="text/plain",
                    glance=text[:200],
                    tags=["benchmark", "memory"],
                    meta=meta,
                ),
            )
        return chunks

    def _stored_contexts(
        self,
        case: BenchmarkCase,
    ) -> list[tuple[str, str, dict[str, Any]]]:
        workspace_id = self._workspace_id(case)
        prefix = f"benchmarks/{case.task_id}/chunks"
        contexts = self.manager.list_contexts(
            workspace_id, prefix=prefix, recursive=True
        )
        rows: list[tuple[str, str, dict[str, Any]]] = []
        for context in contexts:
            meta = dict(context.meta or {})
            rows.append(
                (
                    str(meta.get("chunk_id") or context.name or context.path),
                    context.content or "",
                    {**meta, "path": context.path},
                )
            )
        return sorted(
            rows,
            key=lambda row: (
                int(row[2].get("session_index") or 0),
                str(row[2].get("path") or ""),
            ),
        )

    def _select_context_rows(
        self,
        case: BenchmarkCase,
    ) -> tuple[
        list[tuple[str, str, dict[str, Any]]],
        list[tuple[str, str, dict[str, Any]]],
    ]:
        stored = self._stored_contexts(case)
        chunks = [(chunk_id, content) for chunk_id, content, _ in stored]
        selected = select_lexical_chunks(
            str(case.inputs.get("question") or ""),
            chunks,
            top_k=self.top_k,
        )
        selected_ids = {chunk_id for chunk_id, _ in selected}
        return [row for row in stored if row[0] in selected_ids], stored

    def _new_run(self, case: BenchmarkCase) -> Run:
        return Run(
            id=self._run_id(case),
            workspace_id=self._workspace_id(case),
            user_id=self._user_id(case),
            status=RunStatus.RUNNING,
            trigger_type=TriggerType.USER,
            input_data={"benchmark_task_id": case.task_id},
            input_tokens=0,
            output_tokens=0,
            last_event_sequence=0,
            created_at=datetime.now(UTC),
        )

    def _event(
        self,
        *,
        case: BenchmarkCase,
        run: Run,
        sequence: int,
        event_type: EventType,
        payload: dict[str, Any],
        input_tokens: int = 0,
        output_tokens: int = 0,
        parent_event_id: UUID | None = None,
    ) -> Event:
        event = Event(
            id=uuid5(
                NAMESPACE_URL,
                f"structure-benchmark-event:{case.task_id}:{sequence}:{event_type}",
            ),
            event_type=str(event_type),
            workspace_id=run.workspace_id,
            run_id=run.id,
            user_id=run.user_id,
            payload=payload,
            executor_code="SimpleAgent",
            parent_event_id=parent_event_id,
            sequence=sequence,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            is_archived=False,
            created_at=datetime.now(UTC),
        )
        run.last_event_sequence = max(int(run.last_event_sequence or 0), sequence)
        run.input_tokens = int(run.input_tokens or 0) + max(input_tokens, 0)
        run.output_tokens = int(run.output_tokens or 0) + max(output_tokens, 0)
        return event

    async def _seed_read_context_events(
        self,
        case: BenchmarkCase,
        run: Run,
        selected: list[tuple[str, str, dict[str, Any]]],
    ) -> list[Event]:
        question = str(case.inputs.get("question") or "")
        wants_evidence = bool(
            isinstance(case.reference, dict) and "evidence_ids" in case.reference
        )
        answer_instruction = (
            "Return JSON with keys answer and evidence_ids. Use evidence_ids from "
            "the context content when available."
            if wants_evidence
            else "Return a concise answer, not an explanation."
        )
        events = [
            self._event(
                case=case,
                run=run,
                sequence=1,
                event_type=EventType.USER_MESSAGE,
                payload={
                    "message": (
                        "Answer the benchmark question using structured workspace "
                        f"context only. {answer_instruction}\n\nQuestion: {question}"
                    ),
                    "forced_tools": ["read_context"],
                },
            )
        ]
        tool_calls = [
            {
                "id": f"read-context-{index}",
                "name": "read_context",
                "arguments": {"path": str(meta.get("path") or "")},
            }
            for index, (_chunk_id, _content, meta) in enumerate(selected, start=1)
        ]
        if tool_calls:
            events.append(
                self._event(
                    case=case,
                    run=run,
                    sequence=2,
                    event_type=EventType.AGENT_MESSAGE,
                    payload={"content": "", "tool_calls": tool_calls},
                )
            )
        read_context = ReadContextTool()

        async def skip_db_context(_workspace_id, _path):
            return None

        read_context._read_workspace_context = skip_db_context  # type: ignore[method-assign]
        from structure.services.context import client as context_client

        context_client._manager = self.manager
        sequence = 3
        for index, (chunk_id, _content, meta) in enumerate(selected, start=1):
            path = str(meta.get("path") or "")
            tool_id = f"read-context-{index}"
            call = self._event(
                case=case,
                run=run,
                sequence=sequence,
                event_type=EventType.TOOL_CALL,
                payload={
                    "tool_name": "read_context",
                    "tool_id": tool_id,
                    "arguments": {"path": path},
                },
            )
            events.append(call)
            sequence += 1
            tool_output = await read_context.execute(
                ReadContextTool.InputSchema(
                    workspace_id=str(run.workspace_id),
                    path=path,
                )
            )
            events.append(
                self._event(
                    case=case,
                    run=run,
                    sequence=sequence,
                    event_type=EventType.TOOL_RESULT,
                    payload={
                        "tool_name": "read_context",
                        "tool_id": tool_id,
                        "result": tool_output.model_dump(mode="json"),
                        "benchmark_chunk_id": chunk_id,
                    },
                    parent_event_id=call.id,
                )
            )
            sequence += 1
        return events

    async def _build_in_memory_batches(
        self,
        *,
        events: list[Event],
        run_id: UUID,
    ) -> list[EventBatch]:
        service = ContextBatchService(db=None)  # type: ignore[arg-type]

        async def turn_index_for_event(event: Event) -> int:
            return max(
                sum(
                    1
                    for current in events
                    if current.run_id == event.run_id
                    and current.sequence <= event.sequence
                    and current.event_type == str(EventType.USER_MESSAGE)
                ),
                1,
            )

        service._turn_index_for_event = turn_index_for_event  # type: ignore[method-assign]
        grouped: dict[str, tuple[EventBatch, list[Event]]] = {}
        for event in sorted(events, key=lambda item: (item.sequence, str(item.id))):
            key = await service.context_key_for_event(event)
            if key.context_key not in grouped:
                batch = EventBatch(
                    id=uuid5(
                        NAMESPACE_URL,
                        f"structure-benchmark-batch:{key.context_key}",
                    ),
                    workspace_id=event.workspace_id,
                    run_id=event.run_id,
                    context_key=key.context_key,
                    context_kind=key.context_kind,
                    sequence_start=event.sequence,
                    sequence_end=event.sequence,
                    event_count=0,
                    event_type_counts={},
                    input_tokens=0,
                    output_tokens=0,
                    load_state=ContextBatchLoadState.LOAD_ALL.value,
                    state=ContextBatchState.ACTIVE.value,
                    load_epoch=1,
                    key_content="",
                    key_hash="",
                    meta={},
                )
                grouped[key.context_key] = (batch, [])
            grouped[key.context_key][1].append(event)

        recent_turn_ids = {
            batch.id for batch, _ in grouped.values() if batch.context_kind == "turn"
        }
        batches = []
        for batch, batch_events in grouped.values():
            batch.sequence_start = min(event.sequence for event in batch_events)
            batch.sequence_end = max(event.sequence for event in batch_events)
            batch.event_count = len(batch_events)
            batch.input_tokens = sum(
                int(event.input_tokens or 0) for event in batch_events
            )
            batch.output_tokens = sum(
                int(event.output_tokens or 0) for event in batch_events
            )
            batch.load_state = ContextBatchService._desired_load_state(
                batch,
                recent_turn_ids,
                run_id,
            )
            batch.key_content = ContextBatchService._summary_for_batch(
                batch, batch_events
            )
            batch.meta = {"event_ids": [str(event.id) for event in batch_events]}
            batches.append(batch)
        return sorted(
            batches, key=lambda batch: (batch.sequence_start, batch.context_key)
        )

    def _load_plan_from_batches(
        self,
        *,
        batches: list[EventBatch],
        events: list[Event],
        run_id: UUID,
    ) -> tuple[list[str], list[Event], int]:
        event_by_id = {
            str(event.id): event for event in events if not event.is_archived
        }
        key_contents: list[str] = []
        load_all_events: list[Event] = []
        skipped = 0
        for batch in batches:
            state = batch.load_state
            if batch.run_id == run_id and state != ContextBatchLoadState.NO_LOAD.value:
                state = ContextBatchLoadState.LOAD_ALL.value
            if state == ContextBatchLoadState.NO_LOAD.value:
                skipped += 1
                continue
            if state == ContextBatchLoadState.LOAD_KEY.value:
                if batch.key_content:
                    key_contents.append(batch.key_content)
                continue
            for event_id in (batch.meta or {}).get("event_ids", []):
                event = event_by_id.get(str(event_id))
                if event is not None:
                    load_all_events.append(event)
        deduped = {str(event.id): event for event in load_all_events}
        return (
            key_contents,
            sorted(deduped.values(), key=lambda item: (item.sequence, str(item.id))),
            skipped,
        )

    def _messages_for_events(
        self,
        *,
        run: Run,
        key_contents: list[str],
        load_all_events: list[Event],
    ) -> list[dict[str, Any]]:
        executor = DefaultExecutor(
            {
                "workspace_id": str(run.workspace_id),
                "run_id": str(run.id),
                "api_key": self.api_key or "benchmark-noop",
                "base_url": self.base_url,
                "tools_info": [],
            }
        )
        messages, _tools = executor.get_messages_and_tools_from_batch_plan(
            key_contents=key_contents,
            load_all_events=load_all_events,
        )
        return self._trim_api_messages(
            [message.to_openai_dict() for message in messages]
        )

    def _trim_api_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        total = sum(len(str(message.get("content") or "")) for message in messages)
        if self.max_context_chars <= 0 or total <= self.max_context_chars:
            return messages
        overflow = total - self.max_context_chars
        trimmed = []
        for message in messages:
            current = dict(message)
            content = str(current.get("content") or "")
            if overflow > 0 and current.get("role") in {"system", "tool"} and content:
                remove = min(max(0, len(content) - 200), overflow)
                if remove:
                    current["content"] = content[remove:]
                    overflow -= remove
            trimmed.append(current)
        return trimmed

    @staticmethod
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

    @staticmethod
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

    def _archive_completed_events(self, events: list[Event]) -> dict[str, Any]:
        candidates = EventArchiveService.select_candidates(
            events,
            scope="run",
            strategy=DEFAULT_EVENT_GC_STRATEGY,
            strategy_config=None,
            keep_last=4,
            include_pinned=False,
            event_types=None,
        )
        candidate_ids = {str(event.id) for event in candidates}
        for event in events:
            if str(event.id) in candidate_ids:
                event.is_archived = True
                event.archived_at = datetime.now(UTC)
                event.archive_scope = "run"
                event.archive_reason = "benchmark_event_gc"
        chunks = EventArchiveService._split_event_chunks(
            candidates,
            max_events_per_archive_context=500,
            max_chars_per_archive_context=200_000,
        )
        return {
            "gc_strategy": DEFAULT_EVENT_GC_STRATEGY,
            "gc_keep_last": 4,
            "gc_archived_events": len(candidates),
            "gc_archive_chunks": len(chunks),
            "gc_archived_event_types": [
                str(event.event_type) for event in candidates[:20]
            ],
        }

    async def run(self, case: BenchmarkCase) -> BenchmarkResult:
        self._insert_case_context(case)
        selected_rows, stored_rows = self._select_context_rows(case)
        selected = [(chunk_id, content) for chunk_id, content, _ in selected_rows]
        chunks = [(chunk_id, content) for chunk_id, content, _ in stored_rows]
        run = self._new_run(case)
        events = await self._seed_read_context_events(case, run, selected_rows)
        batches = await self._build_in_memory_batches(events=events, run_id=run.id)
        key_contents, load_all_events, skipped_batches = self._load_plan_from_batches(
            batches=batches,
            events=events,
            run_id=run.id,
        )
        messages = self._messages_for_events(
            run=run,
            key_contents=key_contents,
            load_all_events=load_all_events,
        )
        started = time.monotonic()
        response = await self.reader.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
        )
        latency = time.monotonic() - started
        content = self._choice_content(response)
        usage = getattr(response, "usage", None)
        prompt_tokens = self._usage_value(usage, "prompt_tokens", "input_tokens")
        completion_tokens = self._usage_value(
            usage,
            "completion_tokens",
            "output_tokens",
        )
        cached_tokens = self._usage_value(
            usage,
            "prompt_tokens_details.cached_tokens",
            "cached_tokens",
            "prompt_cache_hit_tokens",
            "cache_read_input_tokens",
        )
        cache_creation_tokens = self._usage_value(
            usage,
            "cache_creation_input_tokens",
            "prompt_cache_miss_tokens",
            "prompt_tokens_details.cache_creation_tokens",
        )
        cache_read_tokens = self._usage_value(
            usage,
            "cache_read_input_tokens",
            "prompt_tokens_details.cached_tokens",
            "cached_tokens",
            "prompt_cache_hit_tokens",
        )
        events.append(
            self._event(
                case=case,
                run=run,
                sequence=int(run.last_event_sequence or 0) + 1,
                event_type=EventType.AGENT_MESSAGE,
                payload={"content": content},
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
            )
        )
        final_batches = await self._build_in_memory_batches(
            events=events, run_id=run.id
        )
        gc_meta = self._archive_completed_events(events)
        usd_cost = (
            int(run.input_tokens or 0) * self.input_cost_per_mtok
            + int(run.output_tokens or 0) * self.output_cost_per_mtok
        ) / 1_000_000
        prompt_chars = sum(
            len(str(message.get("content") or "")) for message in messages
        )
        prompt_tokens_approx = approx_token_count(
            "\n".join(str(message.get("content") or "") for message in messages)
        )
        return BenchmarkResult(
            task_id=case.task_id,
            response=content,
            cost=CostLedger(
                tokens_prompt=int(run.input_tokens or 0),
                tokens_completion=int(run.output_tokens or 0),
                tokens_cached=cached_tokens,
                cache_creation_tokens=cache_creation_tokens,
                cache_read_tokens=cache_read_tokens,
                steps=1,
                tool_calls=len(selected_rows),
                latency_seconds=latency,
                usd_cost=usd_cost,
            ),
            evidence=evidence_from_selected_context(
                case,
                selected,
                response=content,
            ),
            metadata={
                "model": self.model,
                "base_url": self.base_url,
                "context_mode": "structure_event_context",
                "adapter": "StructureMemoryBenchmarkAgent",
                **context_profile(case, chunks, selected),
                "context_data_root": str(self.data_root),
                "run_id": str(run.id),
                "workspace_id": str(run.workspace_id),
                "event_count": len(events),
                "active_event_count_after_gc": sum(
                    1 for event in events if not event.is_archived
                ),
                "batch_count": len(final_batches),
                "batch_key_count": len(key_contents),
                "batch_load_all_event_count": len(load_all_events),
                "batch_skipped_count": skipped_batches,
                "selected_context_paths": [
                    str(meta.get("path") or "") for _, _, meta in selected_rows
                ],
                "prompt_chars": prompt_chars,
                "prompt_tokens_approx": prompt_tokens_approx,
                "max_context_chars": self.max_context_chars,
                "context_truncated": prompt_chars > self.max_context_chars,
                "token_source": "run_event_aggregate",
                "retrieval_source": "context_store_read_context_events",
                "context_batch_event_tokens": {
                    batch.context_key: {
                        "input_tokens": batch.input_tokens,
                        "output_tokens": batch.output_tokens,
                        "load_state": batch.load_state,
                        "event_count": batch.event_count,
                    }
                    for batch in final_batches
                },
                **gc_meta,
            },
        )


@dataclass
class StructurePathMemoryBenchmarkAgent(StructureMemoryBenchmarkAgent):
    """Structure context adapter with deterministic path/time-aware retrieval."""

    def _stored_contexts(
        self,
        case: BenchmarkCase,
    ) -> list[tuple[str, str, dict[str, Any]]]:
        workspace_id = self._workspace_id(case)
        prefix = f"benchmarks/{case.task_id}/chunks"
        contexts = self.manager.list_contexts(
            workspace_id, prefix=prefix, recursive=True
        )
        rows: list[tuple[str, str, dict[str, Any]]] = []
        for context in contexts:
            meta = dict(context.meta or {})
            chunk_id = str(meta.get("chunk_id") or context.name or context.path)
            rows.append(
                (chunk_id, context.content or "", {**meta, "path": context.path})
            )
        return sorted(
            rows,
            key=lambda row: (
                int(row[2].get("session_index") or 0),
                str(row[2].get("path") or ""),
            ),
        )

    def _score_context(
        self,
        *,
        question: str,
        content: str,
        meta: dict[str, Any],
        max_session_index: int,
    ) -> tuple[float, int, float]:
        query_tokens = _tokens(question)
        content_tokens = _tokens(content)
        date_text = str(meta.get("session_date_text") or "")
        path_text = str(meta.get("path") or "")
        lexical_overlap = len(query_tokens & content_tokens)

        temporal = bool(query_tokens & _TEMPORAL_CUES)
        recency = bool(query_tokens & _RECENCY_CUES)
        time_score = 0.0
        if temporal and date_text:
            time_score += 1.0
        if _date_tokens(question) & (_date_tokens(date_text) | _date_tokens(content)):
            time_score += 2.0
        session_index = int(meta.get("session_index") or 0)
        if recency and max_session_index > 0:
            time_score += session_index / max_session_index

        path_score = 0.001 * session_index
        if path_text:
            path_score += 0.0001
        total = lexical_overlap + time_score + path_score
        return total, lexical_overlap, time_score

    def select_contexts_from_store(
        self,
        case: BenchmarkCase,
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        stored = self._stored_contexts(case)
        chunks = [(chunk_id, content) for chunk_id, content, _ in stored]
        if not stored:
            return [], []

        question = str(case.inputs.get("question") or "")
        max_session_index = max(
            int(meta.get("session_index") or 0) for _, _, meta in stored
        )
        ranked = []
        for ordinal, (chunk_id, content, meta) in enumerate(stored):
            score, lexical_overlap, time_score = self._score_context(
                question=question,
                content=content,
                meta=meta,
                max_session_index=max_session_index,
            )
            ranked.append(
                (score, lexical_overlap, time_score, -ordinal, chunk_id, content)
            )

        ranked.sort(reverse=True)
        selected = [
            (chunk_id, content)
            for score, _, _, _, chunk_id, content in ranked[: self.top_k]
            if score > 0
        ]
        if selected:
            return selected, chunks
        return chunks[: self.top_k], chunks

    async def run(self, case: BenchmarkCase) -> BenchmarkResult:
        self._insert_case_context(case)
        selected, chunks = self.select_contexts_from_store(case)
        reader_case = BenchmarkCase(
            task_id=case.task_id,
            inputs={
                "question": case.inputs.get("question"),
                "sessions": [
                    [{"role": chunk_id, "content": text}] for chunk_id, text in selected
                ],
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
            evidence=evidence_from_selected_context(
                case,
                selected,
                response=result.response,
            ),
            metadata={
                **result.metadata,
                "adapter": "StructurePathMemoryBenchmarkAgent",
                **context_profile(case, chunks, selected),
                "context_data_root": str(self.data_root),
                "retrieval_mode": "path_time",
            },
        )
