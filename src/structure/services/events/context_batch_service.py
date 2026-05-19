"""Batch related events into stable context memory units."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.enums.events import (
    ContextBatchLoadState,
    ContextBatchState,
    EventType,
)
from structure.models.events.event import Event
from structure.models.events.event_batch import EventBatch, EventBatchItem

DEFAULT_BATCH_MISC_BUCKET_SIZE = 50
BATCH_INDEX_EXCERPT_LIMIT = 800
BATCH_INDEX_TOTAL_LIMIT = 2400
CONTEXT_TOOL_NAMES = {
    "create_context",
    "delete_context",
    "glob_context",
    "glance_context",
    "list_context",
    "rate_context",
    "read_context",
    "search_context",
    "tree_context",
    "update_context",
}
TRANSIENT_EVENT_TYPES = {
    str(EventType.AGENT_TOKEN),
    str(EventType.AGENT_HEARTBEAT),
    str(EventType.AGENT_THINKING),
    str(EventType.AGENT_PLAN_STEP),
    str(EventType.RUN_STATE_CHANGE),
    str(EventType.TO_EXECUTOR),
    str(EventType.USING_CONTEXT),
}


@dataclass(frozen=True)
class ContextBatchKey:
    """Deterministic key and semantic kind for a context batch."""

    context_key: str
    context_kind: str


@dataclass(frozen=True)
class ContextLoadPlan:
    """Executor-ready batch loading plan."""

    key_contents: list[str] = field(default_factory=list)
    load_all_events: list[Event] = field(default_factory=list)
    skipped_batch_count: int = 0
    batch_ids: list[str] = field(default_factory=list)

    @property
    def has_batches(self) -> bool:
        return bool(
            self.key_contents or self.load_all_events or self.skipped_batch_count
        )


def _event_type_value(event_type: str | EventType) -> str:
    return event_type.value if isinstance(event_type, EventType) else str(event_type)


def _to_uuid(value: UUID | str | None) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    return UUID(str(value))


def _json_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _trim(value: Any, limit: int = 180) -> str:
    text = value if isinstance(value, str) else str(value)
    return text[:limit] + "..." if len(text) > limit else text


def _json_compact(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _payload_text_for_index(event: Event) -> str:
    event_type = _event_type_value(event.event_type)
    payload = event.payload or {}

    if event_type == str(EventType.USER_MESSAGE):
        return _json_compact(payload.get("message", ""))

    if event_type == str(EventType.AGENT_MESSAGE):
        content = payload.get("content") or payload.get("message") or ""
        tool_calls = payload.get("tool_calls") or []
        if tool_calls:
            tool_index = _json_compact(
                [
                    {
                        "id": call.get("id"),
                        "name": call.get("name"),
                        "arguments": call.get("arguments", {}),
                    }
                    for call in tool_calls
                    if isinstance(call, dict)
                ]
            )
            return f"{_json_compact(content)} tool_calls={tool_index}".strip()
        return _json_compact(content)

    if event_type == str(EventType.TOOL_CALL):
        return _json_compact(
            {
                "tool_name": payload.get("tool_name"),
                "tool_id": payload.get("tool_id"),
                "arguments": payload.get("arguments", {}),
            }
        )

    if event_type == str(EventType.TOOL_RESULT):
        return _json_compact(
            {
                "tool_name": payload.get("tool_name"),
                "tool_id": payload.get("tool_id"),
                "result": payload.get("result"),
                "success": payload.get("success"),
            }
        )

    if event_type == str(EventType.TOOL_ERROR):
        return _json_compact(
            {
                "tool_name": payload.get("tool_name"),
                "tool_id": payload.get("tool_id"),
                "error": payload.get("error_message") or payload.get("error"),
            }
        )

    if event_type == str(EventType.USER_FEEDBACK):
        return _json_compact(payload.get("feedback") or payload.get("message") or "")

    return _json_compact(payload)


def _indexed_summary_for_batch(
    batch: EventBatch,
    events: Sequence[Event],
    *,
    excerpt_limit: int = BATCH_INDEX_EXCERPT_LIMIT,
    total_limit: int = BATCH_INDEX_TOTAL_LIMIT,
) -> str:
    lines: list[str] = []
    remaining = total_limit

    for event in sorted(events, key=lambda e: (e.sequence or 0, str(e.id))):
        event_type = _event_type_value(event.event_type)
        text = _payload_text_for_index(event)
        excerpt = _trim(text, excerpt_limit)
        line = f"[seq={event.sequence} type={event_type}] {excerpt}"
        if len(line) > remaining:
            if remaining > 80:
                lines.append(line[: remaining - 30] + "... [batch index truncated]")
            break
        lines.append(line)
        remaining -= len(line) + 1

    if not lines:
        return f"{batch.context_kind} events {batch.sequence_start}-{batch.sequence_end}"
    return "\n".join(lines)


class ContextBatchService:
    """Create, summarize, and plan loading for event context batches."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def assign_events_to_batches(
        self,
        events: Sequence[Event],
        *,
        apply_policy: bool = False,
    ) -> list[EventBatch]:
        batches: list[EventBatch] = []
        for event in sorted(events, key=lambda e: (e.sequence or 0, str(e.id))):
            batches.append(await self.assign_event_to_batch(event))

        if apply_policy and batches:
            workspace_ids = {batch.workspace_id for batch in batches}
            for workspace_id in workspace_ids:
                await self.apply_batch_load_policy(workspace_id)
        return batches

    async def assign_event_to_batch(self, event: Event) -> EventBatch:
        """Assign a persisted event to a deterministic batch."""
        existing = await self.db.execute(
            select(EventBatch)
            .join(EventBatchItem, EventBatchItem.batch_id == EventBatch.id)
            .where(EventBatchItem.event_id == event.id)
        )
        existing_batch = existing.scalar_one_or_none()
        if existing_batch is not None:
            return existing_batch

        batch_key = await self.context_key_for_event(event)
        workspace_id = _to_uuid(event.workspace_id)
        if workspace_id is None:
            raise ValueError("Cannot batch event without workspace_id")

        result = await self.db.execute(
            select(EventBatch).where(
                EventBatch.workspace_id == workspace_id,
                EventBatch.context_key == batch_key.context_key,
            )
        )
        batch = result.scalar_one_or_none()
        if batch is None:
            batch = EventBatch(
                workspace_id=workspace_id,
                run_id=_to_uuid(event.run_id),
                context_key=batch_key.context_key,
                context_kind=batch_key.context_kind,
                sequence_start=event.sequence,
                sequence_end=event.sequence,
                event_count=0,
                event_type_counts={},
                input_tokens=0,
                output_tokens=0,
                load_state=self._initial_load_state(batch_key.context_kind),
                state=ContextBatchState.ACTIVE.value,
                load_epoch=0,
                key_content="",
                key_hash="",
                meta={},
            )
            self.db.add(batch)
            await self.db.flush()

        self.db.add(
            EventBatchItem(
                batch_id=batch.id,
                event_id=event.id,
                sequence=event.sequence,
            )
        )
        await self.db.flush()
        return await self.refresh_batch_key(batch.id)

    async def refresh_batch_key(self, batch_id: str | UUID) -> EventBatch:
        batch_uuid = _to_uuid(batch_id)
        if batch_uuid is None:
            raise ValueError("batch_id is required")

        batch = await self.db.get(EventBatch, batch_uuid)
        if batch is None:
            raise ValueError(f"EventBatch not found: {batch_id}")

        events = await self._load_batch_events(batch_uuid)
        if not events:
            return batch

        type_counts = Counter(_event_type_value(event.event_type) for event in events)
        batch.sequence_start = min(event.sequence for event in events)
        batch.sequence_end = max(event.sequence for event in events)
        batch.event_count = len(events)
        batch.event_type_counts = dict(sorted(type_counts.items()))
        batch.input_tokens = sum(
            max(int(event.input_tokens or 0), 0) for event in events
        )
        batch.output_tokens = sum(
            max(int(event.output_tokens or 0), 0) for event in events
        )
        batch.key_content = self._build_key_content(batch, events)
        batch.key_hash = _json_hash(batch.key_content)
        batch.updated_at = datetime.now(UTC)
        await self.db.flush()
        return batch

    async def context_key_for_event(self, event: Event) -> ContextBatchKey:
        event_type = _event_type_value(event.event_type)
        payload = event.payload or {}
        run_id = str(event.run_id) if event.run_id else "workspace"
        workspace_id = str(event.workspace_id)

        context_path = self._context_path_for_event(event)
        if context_path:
            return ContextBatchKey(
                context_key=f"context:{workspace_id}:{context_path}",
                context_kind="context",
            )

        if event_type in {
            str(EventType.TOOL_CALL),
            str(EventType.TOOL_PENDING),
            str(EventType.TOOL_RESULT),
            str(EventType.TOOL_ERROR),
            str(EventType.TOOL_CLIENT_REQUEST),
        }:
            tool_id = (
                payload.get("tool_id") or payload.get("id") or payload.get("tool_name")
            )
            return ContextBatchKey(
                context_key=f"run:{run_id}:tool:{tool_id or event.sequence}",
                context_kind="tool",
            )

        task_id = payload.get("task_id") or payload.get("id")
        if event_type.startswith("task.") and task_id:
            return ContextBatchKey(
                context_key=f"task:{task_id}",
                context_kind="task",
            )

        artifact_id = payload.get("artifact_id") or payload.get("id")
        if event_type.startswith("artifact.") and artifact_id:
            return ContextBatchKey(
                context_key=f"artifact:{artifact_id}",
                context_kind="artifact",
            )

        if event_type in {
            str(EventType.USER_MESSAGE),
            str(EventType.AGENT_MESSAGE),
            str(EventType.USER_FEEDBACK),
        }:
            turn_index = await self._turn_index_for_event(event)
            return ContextBatchKey(
                context_key=f"run:{run_id}:turn:{turn_index}",
                context_kind="turn",
            )

        if event_type in TRANSIENT_EVENT_TYPES:
            bucket = max(int(event.sequence or 0) // DEFAULT_BATCH_MISC_BUCKET_SIZE, 0)
            return ContextBatchKey(
                context_key=f"run:{run_id}:transient:{bucket}",
                context_kind="transient",
            )

        bucket = max(int(event.sequence or 0) // DEFAULT_BATCH_MISC_BUCKET_SIZE, 0)
        return ContextBatchKey(
            context_key=f"run:{run_id}:misc:{bucket}",
            context_kind="misc",
        )

    async def apply_batch_load_policy(
        self,
        workspace_id: str | UUID,
        *,
        run_id: str | UUID | None = None,
    ) -> None:
        """Apply the stable epoch load-state policy at a safe boundary."""
        workspace_uuid = _to_uuid(workspace_id)
        run_uuid = _to_uuid(run_id)
        if workspace_uuid is None:
            return

        result = await self.db.execute(
            select(EventBatch)
            .where(
                EventBatch.workspace_id == workspace_uuid,
                EventBatch.state == ContextBatchState.ACTIVE.value,
            )
            .order_by(EventBatch.sequence_start.asc(), EventBatch.created_at.asc())
        )
        batches = list(result.scalars().all())
        if not batches:
            return

        max_epoch = max((batch.load_epoch or 0) for batch in batches)
        next_epoch = max_epoch + 1
        turn_batches = [batch for batch in batches if batch.context_kind == "turn"]
        recent_turn_ids = {batch.id for batch in turn_batches[-2:]}
        state_changed_batch_ids: list[UUID] = []

        for batch in batches:
            desired = self._desired_load_state(batch, recent_turn_ids, run_uuid)
            state_changed = desired != batch.load_state
            if desired != batch.load_state or batch.load_epoch != next_epoch:
                batch.load_state = desired
                batch.load_epoch = next_epoch
                batch.updated_at = datetime.now(UTC)
                if state_changed:
                    state_changed_batch_ids.append(batch.id)
        await self.db.flush()

        for batch_id in state_changed_batch_ids:
            await self.refresh_batch_key(batch_id)

    async def build_load_plan(
        self,
        workspace_id: str | UUID,
        *,
        run_id: str | UUID | None = None,
        current_events: Sequence[Event] | None = None,
    ) -> ContextLoadPlan:
        workspace_uuid = _to_uuid(workspace_id)
        run_uuid = _to_uuid(run_id)
        if workspace_uuid is None:
            return ContextLoadPlan()

        result = await self.db.execute(
            select(EventBatch)
            .where(
                EventBatch.workspace_id == workspace_uuid,
                EventBatch.state == ContextBatchState.ACTIVE.value,
            )
            .order_by(
                EventBatch.load_epoch.asc(),
                EventBatch.sequence_start.asc(),
                EventBatch.context_key.asc(),
            )
        )
        batches = list(result.scalars().all())
        if not batches and not current_events:
            return ContextLoadPlan()

        def effective_load_state(batch: EventBatch) -> str:
            if (
                run_uuid is not None
                and batch.run_id == run_uuid
                and batch.load_state != ContextBatchLoadState.NO_LOAD.value
            ):
                return ContextBatchLoadState.LOAD_ALL.value
            return batch.load_state

        key_contents = [
            batch.key_content
            for batch in batches
            if effective_load_state(batch) == ContextBatchLoadState.LOAD_KEY.value
            and batch.key_content
        ]
        skipped_count = sum(
            1
            for batch in batches
            if effective_load_state(batch) == ContextBatchLoadState.NO_LOAD.value
        )
        load_all_batch_ids = [
            batch.id
            for batch in batches
            if effective_load_state(batch) == ContextBatchLoadState.LOAD_ALL.value
        ]
        load_all_events = await self._load_events_for_batches(load_all_batch_ids)

        seen_ids = {str(event.id) for event in load_all_events}
        for event in sorted(
            current_events or [], key=lambda e: (e.sequence or 0, str(e.id))
        ):
            if str(event.id) not in seen_ids:
                load_all_events.append(event)
                seen_ids.add(str(event.id))

        return ContextLoadPlan(
            key_contents=key_contents,
            load_all_events=load_all_events,
            skipped_batch_count=skipped_count,
            batch_ids=[str(batch.id) for batch in batches],
        )

    async def _turn_index_for_event(self, event: Event) -> int:
        if not event.run_id:
            return 1
        event_type = _event_type_value(event.event_type)
        if event_type == str(EventType.USER_MESSAGE):
            stmt = select(func.count(Event.id)).where(
                Event.run_id == _to_uuid(event.run_id),
                Event.sequence <= event.sequence,
                Event.event_type == str(EventType.USER_MESSAGE),
            )
        else:
            stmt = select(func.count(Event.id)).where(
                Event.run_id == _to_uuid(event.run_id),
                Event.sequence <= event.sequence,
                Event.event_type == str(EventType.USER_MESSAGE),
            )
        count = await self.db.scalar(stmt)
        return max(int(count or 0), 1)

    @staticmethod
    def _context_path_for_event(event: Event) -> str | None:
        payload = event.payload or {}
        tool_name = str(payload.get("tool_name") or "")
        if tool_name not in CONTEXT_TOOL_NAMES:
            return None

        candidates = [
            (payload.get("arguments") or {}).get("path"),
            (payload.get("arguments") or {}).get("prefix"),
            (payload.get("arguments") or {}).get("pattern"),
            ((payload.get("result") or {}).get("data") or {}).get("path"),
            (payload.get("result") or {}).get("path"),
        ]
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip().lstrip("/")
        return None

    @staticmethod
    def _initial_load_state(context_kind: str) -> str:
        if context_kind in {"transient", "misc"}:
            return ContextBatchLoadState.NO_LOAD.value
        return ContextBatchLoadState.LOAD_ALL.value

    @staticmethod
    def _desired_load_state(
        batch: EventBatch,
        recent_turn_ids: set[UUID],
        current_run_id: UUID | None,
    ) -> str:
        if batch.context_kind in {"transient", "misc"}:
            return ContextBatchLoadState.NO_LOAD.value
        if batch.context_kind == "turn" and batch.id in recent_turn_ids:
            return ContextBatchLoadState.LOAD_ALL.value
        if (
            current_run_id is not None
            and batch.run_id == current_run_id
            and batch.context_kind == "tool"
        ):
            return ContextBatchLoadState.LOAD_ALL.value
        return ContextBatchLoadState.LOAD_KEY.value

    async def _load_batch_events(self, batch_id: UUID) -> list[Event]:
        result = await self.db.execute(
            select(Event)
            .join(EventBatchItem, EventBatchItem.event_id == Event.id)
            .where(EventBatchItem.batch_id == batch_id)
            .order_by(EventBatchItem.sequence.asc(), Event.created_at.asc())
        )
        return list(result.scalars().all())

    async def _load_events_for_batches(self, batch_ids: Iterable[UUID]) -> list[Event]:
        ids = list(batch_ids)
        if not ids:
            return []
        result = await self.db.execute(
            select(Event)
            .join(EventBatchItem, EventBatchItem.event_id == Event.id)
            .join(EventBatch, EventBatch.id == EventBatchItem.batch_id)
            .where(
                EventBatchItem.batch_id.in_(ids),
                Event.is_archived.is_(False),
            )
            .order_by(Event.sequence.asc(), Event.created_at.asc(), Event.id.asc())
        )
        return list(result.scalars().all())

    def _build_key_content(self, batch: EventBatch, events: Sequence[Event]) -> str:
        summary = self._summary_for_batch(batch, events)
        event_type_counts = json.dumps(
            dict(
                sorted(
                    Counter(
                        _event_type_value(event.event_type) for event in events
                    ).items()
                )
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        return "\n".join(
            [
                f"batch_key={batch.context_key}",
                f"kind={batch.context_kind}",
                f"state={batch.load_state}",
                f"sequence={batch.sequence_start}-{batch.sequence_end}",
                f"events={len(events)}",
                f"types={event_type_counts}",
                f"tokens=input:{batch.input_tokens},output:{batch.output_tokens}",
                f"summary={summary}",
            ]
        )

    @staticmethod
    def _summary_for_batch(batch: EventBatch, events: Sequence[Event]) -> str:
        if batch.context_kind in {"turn", "tool"}:
            return _indexed_summary_for_batch(batch, events)

        if batch.context_kind == "context":
            return f"context_path={batch.context_key.rsplit(':', 1)[-1]}"

        return (
            f"{batch.context_kind} events {batch.sequence_start}-{batch.sequence_end}"
        )


async def rebuild_batches_for_events(
    db: AsyncSession,
    events: Sequence[Event],
    *,
    apply_policy: bool = False,
) -> list[EventBatch]:
    """Convenience wrapper used by worker/archive paths."""
    return await ContextBatchService(db).assign_events_to_batches(
        events,
        apply_policy=apply_policy,
    )


__all__ = [
    "ContextBatchKey",
    "ContextBatchService",
    "ContextLoadPlan",
    "rebuild_batches_for_events",
]
