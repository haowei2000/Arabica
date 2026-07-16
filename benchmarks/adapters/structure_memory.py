"""Structure-backed memory adapter for benchmark runs.

This adapter exercises the repository's file-based context service as the
memory store.  Each benchmark case gets an isolated synthetic workspace and
its history is ingested as *multiple prior runs* (one per session), so the
context-batch load policy and event GC operate exactly as they do in the
platform: prior-run turn batches outside the recent window materialise at
``LOAD_KEY`` (compact batch keys), the current query run materialises at
``LOAD_ALL``, and event GC gates prior-run visibility before the reader
prompt is assembled.  Chunks are additionally inserted as context entries
and query-time retrieval selects among them before handing context to a
fixed OpenAI-compatible reader model.  Per-case mechanism-activation
counters are recorded per ``benchmarks/PROTOCOL.md`` §2.
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
from structure.frameworks.tool_calling import ChatMessage
from structure.models.events.event import Event
from structure.models.events.event_batch import EventBatch
from structure.models.runs.run import Run
from structure.plugins.executors.default.concrete import (
    RUNTIME_CONTEXT_TEMPLATE,
    SYSTEM_PROMPT,
    _events_to_messages,
    _is_tool_schema_context_result,
)
from structure.plugins.tools.context.read_context import ReadContextTool
from structure.schemas.events.event_payloads import EventType
from structure.services.context_service.manager import ContextManager
from structure.services.context_service.models import ContextCreateRequest
from structure.services.context_service.rating import (
    DEFAULT_RATING_ALPHA,
    blend_score,
    passes_rating_threshold,
    rating_average,
)
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
_AGENT_ROLES = {"assistant", "agent", "ai", "model"}
# Mirrors ContextBatchService.apply_batch_load_policy: only the last two
# turn batches in the workspace stay at LOAD_ALL; older turns compact to keys.
_RECENT_TURN_WINDOW = 2


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
    # Rating read-path knobs (PROTOCOL.md §7, ablation A2). With no recorded
    # ratings the defaults are behaviour-preserving: every entry passes the
    # threshold and ranks by its base retrieval score.
    use_ratings: bool = True
    rating_alpha: float = DEFAULT_RATING_ALPHA
    min_rating: float | None = None

    retrieval_mode = "lexical"

    def __post_init__(self) -> None:
        self.manager = ContextManager(str(self.data_root))
        self._case_ratings: dict[str, dict[str, tuple[float, int]]] = {}
        self._rating_stats: dict[str, dict[str, Any]] = {}
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

    def record_context_rating(
        self, case: BenchmarkCase, path: str, rating: float
    ) -> None:
        """Fold a source rating into the per-case denormalised aggregate.

        Mirrors ``rate_context``'s ``rating_sum``/``rating_count`` write path
        for the file-backed benchmark store; the read path consumes it via
        :mod:`structure.services.context_service.rating`.
        """
        ratings = self._case_ratings.setdefault(case.task_id, {})
        current_sum, current_count = ratings.get(path, (0.0, 0))
        ratings[path] = (current_sum + float(rating), current_count + 1)

    def _rating_avg_for(
        self, case: BenchmarkCase, meta: dict[str, Any]
    ) -> float | None:
        path = str(meta.get("path") or "")
        entry = self._case_ratings.get(case.task_id, {}).get(path)
        if entry is not None:
            return rating_average(entry[0], entry[1])
        return rating_average(meta.get("rating_sum"), meta.get("rating_count"))

    def _apply_rating_read_path(
        self,
        case: BenchmarkCase,
        stored: list[tuple[str, str, dict[str, Any]]],
    ) -> tuple[list[tuple[str, str, dict[str, Any]]], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": bool(self.use_ratings),
            "alpha": self.rating_alpha,
            "min_rating": self.min_rating,
            "rated_entries": 0,
            "filtered_by_rating": 0,
            "blend_applied": False,
        }
        if not self.use_ratings:
            return list(stored), stats
        kept: list[tuple[str, str, dict[str, Any]]] = []
        for row in stored:
            avg = self._rating_avg_for(case, row[2])
            if avg is not None:
                stats["rated_entries"] += 1
            if passes_rating_threshold(avg, self.min_rating):
                kept.append(row)
            else:
                stats["filtered_by_rating"] += 1
        return kept, stats

    def _select_context_rows(
        self,
        case: BenchmarkCase,
    ) -> tuple[
        list[tuple[str, str, dict[str, Any]]],
        list[tuple[str, str, dict[str, Any]]],
    ]:
        stored = self._stored_contexts(case)
        candidates, rating_stats = self._apply_rating_read_path(case, stored)
        self._rating_stats[case.task_id] = rating_stats
        chunks = [(chunk_id, content) for chunk_id, content, _ in candidates]
        selected = select_lexical_chunks(
            str(case.inputs.get("question") or ""),
            chunks,
            top_k=self.top_k,
        )
        selected_ids = {chunk_id for chunk_id, _ in selected}
        return [row for row in candidates if row[0] in selected_ids], stored

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

    def _session_run(self, case: BenchmarkCase, session_index: int) -> Run:
        return Run(
            id=uuid5(
                NAMESPACE_URL,
                f"structure-benchmark-session-run:{case.task_id}:{session_index}",
            ),
            workspace_id=self._workspace_id(case),
            user_id=self._user_id(case),
            status=RunStatus.FINISHED,
            trigger_type=TriggerType.USER,
            input_data={
                "benchmark_task_id": case.task_id,
                "session_index": session_index,
            },
            input_tokens=0,
            output_tokens=0,
            last_event_sequence=0,
            created_at=datetime.now(UTC),
        )

    def _ingest_history_runs(
        self, case: BenchmarkCase
    ) -> tuple[list[Run], list[Event], int]:
        """Replay case history as finished prior runs in the workspace.

        Each session becomes its own run whose turns are USER/AGENT message
        events with a workspace-global sequence, so the batch load policy
        sees genuine multi-run history instead of one fresh run per case.
        Returns the prior runs, their events, and the next free sequence.
        """
        sessions = case.inputs.get("sessions") or []
        runs: list[Run] = []
        events: list[Event] = []
        sequence = 1
        if not isinstance(sessions, list):
            return runs, events, sequence
        for index, session in enumerate(sessions, start=1):
            if not isinstance(session, list) or not session:
                continue
            run = self._session_run(case, index)
            emitted = False
            for turn in session:
                if isinstance(turn, dict):
                    content = str(turn.get("content") or turn.get("text") or "")
                    role = str(turn.get("role") or "user").lower()
                else:
                    content, role = str(turn), "user"
                if not content:
                    continue
                if role in _AGENT_ROLES:
                    event_type = EventType.AGENT_MESSAGE
                    payload: dict[str, Any] = {"content": content}
                else:
                    event_type = EventType.USER_MESSAGE
                    payload = {"message": content, "speaker": role}
                events.append(
                    self._event(
                        case=case,
                        run=run,
                        sequence=sequence,
                        event_type=event_type,
                        payload=payload,
                    )
                )
                sequence += 1
                emitted = True
            if emitted:
                runs.append(run)
        return runs, events, sequence

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
        *,
        start_sequence: int = 1,
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
        sequence = start_sequence
        events = [
            self._event(
                case=case,
                run=run,
                sequence=sequence,
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
        sequence += 1
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
                    sequence=sequence,
                    event_type=EventType.AGENT_MESSAGE,
                    payload={"content": "", "tool_calls": tool_calls},
                )
            )
            sequence += 1
        read_context = ReadContextTool()

        async def skip_db_context(_workspace_id, _path):
            return None

        read_context._read_workspace_context = skip_db_context  # type: ignore[method-assign]
        from structure.services.context import client as context_client

        context_client._manager = self.manager
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

        turn_batch_ids = [
            batch.id for batch, _ in grouped.values() if batch.context_kind == "turn"
        ]
        recent_turn_ids = set(turn_batch_ids[-_RECENT_TURN_WINDOW:])
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
    ) -> tuple[list[str], list[Event], dict[str, int]]:
        event_by_id = {
            str(event.id): event for event in events if not event.is_archived
        }
        key_contents: list[str] = []
        load_all_events: list[Event] = []
        stats = {
            "load_key_batches": 0,
            "load_all_batches": 0,
            "no_load_batches": 0,
            "empty_key_batches": 0,
        }
        for batch in batches:
            state = batch.load_state
            if batch.run_id == run_id and state != ContextBatchLoadState.NO_LOAD.value:
                state = ContextBatchLoadState.LOAD_ALL.value
            if state == ContextBatchLoadState.NO_LOAD.value:
                stats["no_load_batches"] += 1
                continue
            if state == ContextBatchLoadState.LOAD_KEY.value:
                stats["load_key_batches"] += 1
                if batch.key_content:
                    key_contents.append(batch.key_content)
                else:
                    stats["empty_key_batches"] += 1
                continue
            stats["load_all_batches"] += 1
            for event_id in (batch.meta or {}).get("event_ids", []):
                event = event_by_id.get(str(event_id))
                if event is not None:
                    load_all_events.append(event)
        deduped = {str(event.id): event for event in load_all_events}
        return (
            key_contents,
            sorted(deduped.values(), key=lambda item: (item.sequence, str(item.id))),
            stats,
        )

    def _messages_for_events(
        self,
        *,
        run: Run,
        key_contents: list[str],
        load_all_events: list[Event],
    ) -> list[dict[str, Any]]:
        conv_events = [
            event
            for event in load_all_events
            if not _is_tool_schema_context_result(event)
        ]
        messages = [
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            *(
                ChatMessage(role="system", content=f"Context batch key:\n{content}")
                for content in key_contents
            ),
            *_events_to_messages(conv_events),
        ]
        runtime_context = ChatMessage(
            role="system",
            content=RUNTIME_CONTEXT_TEMPLATE.format(
                workspace_id=str(run.workspace_id),
                run_id=str(run.id),
            ),
        )
        for idx in range(len(messages) - 1, 0, -1):
            if messages[idx].role == "user":
                messages = [*messages[:idx], runtime_context, *messages[idx:]]
                break
        else:
            messages.append(runtime_context)
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

    def _archive_completed_events(
        self, events: list[Event], *, scope: str = "workspace"
    ) -> dict[str, Any]:
        """Apply the default event-GC policy in-loop, before materialisation.

        Only prior-run history is passed in: the platform's load plan always
        keeps the current run fully visible, so GC gates what older runs
        contribute to the prompt rather than the in-flight plan--act cycle.
        """
        candidates = EventArchiveService.select_candidates(
            events,
            scope=scope,
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
                event.archive_scope = scope
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
        history_runs, history_events, next_sequence = self._ingest_history_runs(case)
        run = self._new_run(case)
        query_events = await self._seed_read_context_events(
            case, run, selected_rows, start_sequence=next_sequence
        )
        # In-loop GC over prior-run history, applied BEFORE the load plan so
        # archived events are gated out of the prompt, not just annotated.
        gc_meta = self._archive_completed_events(history_events)
        events = [*history_events, *query_events]
        batches = await self._build_in_memory_batches(events=events, run_id=run.id)
        key_contents, load_all_events, load_stats = self._load_plan_from_batches(
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
                "adapter": type(self).__name__,
                "retrieval_mode": self.retrieval_mode,
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
                "batch_skipped_count": load_stats["no_load_batches"],
                "selected_context_paths": [
                    str(meta.get("path") or "") for _, _, meta in selected_rows
                ],
                "prompt_chars": prompt_chars,
                "prompt_tokens_approx": prompt_tokens_approx,
                "max_context_chars": self.max_context_chars,
                "context_truncated": prompt_chars > self.max_context_chars,
                "token_source": "run_event_aggregate",
                "retrieval_source": "context_store_read_context_events",
                # PROTOCOL.md §2: a B3 run is valid only if these confirm the
                # context machinery actually fired for this case.
                "mechanism_activation": {
                    "multi_run_ingestion": bool(history_runs),
                    "prior_run_count": len(history_runs),
                    "history_event_count": len(history_events),
                    **load_stats,
                    "gc_applied": True,
                    "gc_archived_events": gc_meta["gc_archived_events"],
                    "recent_turn_window": _RECENT_TURN_WINDOW,
                    "rating_read_path": self._rating_stats.pop(
                        case.task_id,
                        {
                            "enabled": bool(self.use_ratings),
                            "alpha": self.rating_alpha,
                            "min_rating": self.min_rating,
                            "rated_entries": 0,
                            "filtered_by_rating": 0,
                            "blend_applied": False,
                        },
                    ),
                },
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
    """Structure context adapter with deterministic path/time-aware retrieval.

    Only retrieval differs from the parent: cases run through the same
    event-driven multi-run pipeline (batch load plan, in-loop GC), so B3
    exercises the context machinery required by PROTOCOL.md §2.
    """

    retrieval_mode = "path_time"

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

    def _select_context_rows(
        self,
        case: BenchmarkCase,
    ) -> tuple[
        list[tuple[str, str, dict[str, Any]]],
        list[tuple[str, str, dict[str, Any]]],
    ]:
        stored = self._stored_contexts(case)
        if not stored:
            return [], []
        candidates, rating_stats = self._apply_rating_read_path(case, stored)
        if not candidates:
            candidates = list(stored)

        question = str(case.inputs.get("question") or "")
        max_session_index = max(
            int(meta.get("session_index") or 0) for _, _, meta in candidates
        )
        scored = []
        for ordinal, (chunk_id, content, meta) in enumerate(candidates):
            score, lexical_overlap, time_score = self._score_context(
                question=question,
                content=content,
                meta=meta,
                max_session_index=max_session_index,
            )
            scored.append((score, lexical_overlap, time_score, ordinal, chunk_id))

        # Ranking stage of the rating read-path: normalise the deterministic
        # retrieval score to [0, 1] and blend with the rating mean. Without
        # ratings the blend is a monotone transform, so ordering is unchanged.
        max_score = max((entry[0] for entry in scored), default=0.0)
        ranked = []
        for score, lexical_overlap, time_score, ordinal, chunk_id in scored:
            similarity = score / max_score if max_score > 0 else 0.0
            rating_avg = (
                self._rating_avg_for(case, candidates[ordinal][2])
                if rating_stats["enabled"]
                else None
            )
            if rating_avg is not None:
                rating_stats["blend_applied"] = True
            blended = blend_score(similarity, rating_avg, alpha=self.rating_alpha)
            ranked.append((blended, lexical_overlap, time_score, -ordinal, chunk_id))
        self._rating_stats[case.task_id] = rating_stats

        ranked.sort(reverse=True)
        by_chunk_id = {row[0]: row for row in candidates}
        selected_rows = [
            by_chunk_id[chunk_id]
            for blended, _, _, _, chunk_id in ranked[: self.top_k]
            if blended > 0 and chunk_id in by_chunk_id
        ]
        if not selected_rows:
            selected_rows = candidates[: self.top_k]
        return selected_rows, stored

    def select_contexts_from_store(
        self,
        case: BenchmarkCase,
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        selected_rows, stored = self._select_context_rows(case)
        return (
            [(chunk_id, content) for chunk_id, content, _ in selected_rows],
            [(chunk_id, content) for chunk_id, content, _ in stored],
        )
