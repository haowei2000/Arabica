"""Archive active event memory without deleting the audit log."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.enums import EventType
from structure.core.enums.context import ContextScope, ContextType
from structure.core.enums.events import ContextBatchState
from structure.models.context.context import Context
from structure.models.events.event import Event
from structure.models.events.event_batch import EventBatch, EventBatchItem
from structure.services.events.event_gc import (
    DEFAULT_EVENT_GC_STRATEGY,
    EventGCStrategyRegistry,
)
from structure.utils.workspace_context_cache import invalidate_workspace_context_cache

DEFAULT_ARCHIVE_MAX_EVENTS_PER_CONTEXT = 500
DEFAULT_ARCHIVE_MAX_CHARS_PER_CONTEXT = 200_000
DEFAULT_ARCHIVE_UPDATE_CHUNK_SIZE = 1000
DEFAULT_ARCHIVE_LOAD_CHUNK_SIZE = 1000


@dataclass(frozen=True)
class EventArchiveCandidate:
    """Lightweight event row used for GC strategy selection."""

    id: UUID
    event_type: str | EventType
    workspace_id: UUID
    run_id: UUID | None
    sequence: int
    created_at: datetime | None
    batch_id: UUID | None = None
    batch_context_key: str | None = None
    batch_load_state: str | None = None


@dataclass(frozen=True)
class EventArchiveResult:
    """Summary returned after a run/workspace archive operation."""

    scope: str
    scope_id: str
    dry_run: bool
    archived_count: int
    skipped_count: int
    active_count_before: int
    archive_context_id: str | None
    archive_path: str | None
    reason: str
    strategy: str
    archive_context_ids: list[str] = field(default_factory=list)
    archive_paths: list[str] = field(default_factory=list)
    archive_chunks: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "scope_id": self.scope_id,
            "dry_run": self.dry_run,
            "archived_count": self.archived_count,
            "skipped_count": self.skipped_count,
            "active_count_before": self.active_count_before,
            "archive_context_id": self.archive_context_id,
            "archive_path": self.archive_path,
            "reason": self.reason,
            "strategy": self.strategy,
            "archive_context_ids": self.archive_context_ids,
            "archive_paths": self.archive_paths,
            "archive_chunks": self.archive_chunks,
        }


def _event_type_value(event_type: str | EventType) -> str:
    return event_type.value if isinstance(event_type, EventType) else str(event_type)


def _trim(value: Any, limit: int = 300) -> str:
    text = value if isinstance(value, str) else str(value)
    return text[:limit] + "..." if len(text) > limit else text


def _event_context_line(event: Event) -> str:
    rendered = event.to_context()
    if rendered:
        return rendered
    return _trim(event.payload or {}, 180)


def select_archive_candidates(
    events: Sequence[Event],
    *,
    keep_last: int | None,
    include_pinned: bool,
    event_types: Iterable[str] | None = None,
    scope: str = "run",
    strategy: str = DEFAULT_EVENT_GC_STRATEGY,
    strategy_config: Mapping[str, Any] | None = None,
) -> list[Event]:
    """Backward-compatible wrapper around the registered GC strategy."""
    return list(
        EventArchiveService.select_candidates(
            events,
            scope=scope,
            strategy=strategy,
            strategy_config=strategy_config,
            keep_last=keep_last,
            include_pinned=include_pinned,
            event_types=event_types,
        )
    )


class EventArchiveService:
    """Archive events from active run/workspace memory into Context rows.

    Archiving is intentionally soft: Event rows remain in the audit log with
    archive metadata, while active-memory queries and executor history default
    to ``is_archived = false``.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def archive_run_memory(
        self,
        run_id: str | UUID,
        *,
        user_id: str | UUID,
        keep_last: int | None = None,
        include_pinned: bool = False,
        event_types: Iterable[str] | None = None,
        strategy: str = DEFAULT_EVENT_GC_STRATEGY,
        strategy_config: Mapping[str, Any] | None = None,
        dry_run: bool = False,
        reason: str = "manual_event_gc",
        max_events_per_archive_context: int = DEFAULT_ARCHIVE_MAX_EVENTS_PER_CONTEXT,
        max_chars_per_archive_context: int = DEFAULT_ARCHIVE_MAX_CHARS_PER_CONTEXT,
        bulk_update_chunk_size: int = DEFAULT_ARCHIVE_UPDATE_CHUNK_SIZE,
    ) -> EventArchiveResult:
        run_uuid = run_id if isinstance(run_id, UUID) else UUID(str(run_id))
        user_uuid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))

        events = await self._load_run_event_candidates(run_uuid)
        candidates = self.select_candidates(
            events,
            scope="run",
            strategy=strategy,
            strategy_config=strategy_config,
            keep_last=keep_last,
            include_pinned=include_pinned,
            event_types=event_types,
        )
        if dry_run or not candidates:
            return self._result(
                scope="run",
                scope_id=str(run_uuid),
                dry_run=dry_run,
                active_count_before=len(events),
                candidates=candidates,
                archive_contexts=None,
                reason=reason,
                strategy=strategy,
            )

        candidate_events = await self._load_full_events(candidates)
        if not candidate_events:
            return self._result(
                scope="run",
                scope_id=str(run_uuid),
                dry_run=False,
                active_count_before=len(events),
                candidates=[],
                archive_contexts=None,
                reason=reason,
                strategy=strategy,
            )

        workspace_id = candidate_events[0].workspace_id
        workspace_uuid = UUID(str(workspace_id))
        archived_chunks = await self._create_archive_contexts(
            scope="run",
            scope_id=str(run_uuid),
            user_id=user_uuid,
            source_id=workspace_uuid,
            context_scope=ContextScope.WORKSPACE,
            workspace_id=workspace_uuid,
            run_id=run_uuid,
            events=candidate_events,
            reason=reason,
            max_events_per_archive_context=max_events_per_archive_context,
            max_chars_per_archive_context=max_chars_per_archive_context,
        )
        for chunk_events, archive_context in archived_chunks:
            await self._mark_archived_bulk(
                [event.id for event in chunk_events],
                archive_context,
                "run",
                reason,
                chunk_size=bulk_update_chunk_size,
            )
        await self.db.flush()
        if workspace_id:
            invalidate_workspace_context_cache(str(workspace_id))
        return self._result(
            scope="run",
            scope_id=str(run_uuid),
            dry_run=False,
            active_count_before=len(events),
            candidates=candidate_events,
            archive_contexts=[ctx for _, ctx in archived_chunks],
            reason=reason,
            strategy=strategy,
        )

    async def archive_workspace_memory(
        self,
        workspace_id: str | UUID,
        *,
        user_id: str | UUID,
        keep_last: int | None = None,
        include_pinned: bool = False,
        include_run_events: bool = True,
        event_types: Iterable[str] | None = None,
        strategy: str = DEFAULT_EVENT_GC_STRATEGY,
        strategy_config: Mapping[str, Any] | None = None,
        dry_run: bool = False,
        reason: str = "manual_event_gc",
        max_events_per_archive_context: int = DEFAULT_ARCHIVE_MAX_EVENTS_PER_CONTEXT,
        max_chars_per_archive_context: int = DEFAULT_ARCHIVE_MAX_CHARS_PER_CONTEXT,
        bulk_update_chunk_size: int = DEFAULT_ARCHIVE_UPDATE_CHUNK_SIZE,
    ) -> EventArchiveResult:
        workspace_uuid = (
            workspace_id if isinstance(workspace_id, UUID) else UUID(str(workspace_id))
        )
        user_uuid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))

        conditions = [
            Event.workspace_id == workspace_uuid,
            Event.is_archived.is_(False),
        ]
        if not include_run_events:
            conditions.append(Event.run_id.is_(None))

        events = await self._load_workspace_event_candidates(conditions)
        candidates = self.select_candidates(
            events,
            scope="workspace",
            strategy=strategy,
            strategy_config=strategy_config,
            keep_last=keep_last,
            include_pinned=include_pinned,
            event_types=event_types,
        )
        if dry_run or not candidates:
            return self._result(
                scope="workspace",
                scope_id=str(workspace_uuid),
                dry_run=dry_run,
                active_count_before=len(events),
                candidates=candidates,
                archive_contexts=None,
                reason=reason,
                strategy=strategy,
            )

        candidate_events = await self._load_full_events(candidates)
        if not candidate_events:
            return self._result(
                scope="workspace",
                scope_id=str(workspace_uuid),
                dry_run=False,
                active_count_before=len(events),
                candidates=[],
                archive_contexts=None,
                reason=reason,
                strategy=strategy,
            )

        archived_chunks = await self._create_archive_contexts(
            scope="workspace",
            scope_id=str(workspace_uuid),
            user_id=user_uuid,
            source_id=workspace_uuid,
            context_scope=ContextScope.WORKSPACE,
            workspace_id=workspace_uuid,
            run_id=None,
            events=candidate_events,
            reason=reason,
            max_events_per_archive_context=max_events_per_archive_context,
            max_chars_per_archive_context=max_chars_per_archive_context,
        )
        for chunk_events, archive_context in archived_chunks:
            await self._mark_archived_bulk(
                [event.id for event in chunk_events],
                archive_context,
                "workspace",
                reason,
                chunk_size=bulk_update_chunk_size,
            )
        await self.db.flush()
        invalidate_workspace_context_cache(str(workspace_uuid))
        return self._result(
            scope="workspace",
            scope_id=str(workspace_uuid),
            dry_run=False,
            active_count_before=len(events),
            candidates=candidate_events,
            archive_contexts=[ctx for _, ctx in archived_chunks],
            reason=reason,
            strategy=strategy,
        )

    @staticmethod
    def select_candidates(
        events: Sequence[Any],
        *,
        scope: str,
        strategy: str,
        strategy_config: Mapping[str, Any] | None,
        keep_last: int | None,
        include_pinned: bool,
        event_types: Iterable[str] | None,
    ) -> list[Any]:
        cfg = dict(strategy_config or {})
        cfg.setdefault("include_pinned", include_pinned)
        if event_types is not None:
            cfg.setdefault("event_types", list(event_types))
        if keep_last is not None:
            cfg.setdefault("keep_last_floor", keep_last)

        gc_strategy = EventGCStrategyRegistry.get(strategy)
        return list(gc_strategy.select_candidates(events, scope=scope, config=cfg))

    async def _load_run_event_candidates(
        self,
        run_id: UUID,
    ) -> list[EventArchiveCandidate]:
        result = await self.db.execute(
            select(
                Event.id,
                Event.event_type,
                Event.workspace_id,
                Event.run_id,
                Event.sequence,
                Event.created_at,
                EventBatch.id,
                EventBatch.context_key,
                EventBatch.load_state,
            )
            .outerjoin(EventBatchItem, EventBatchItem.event_id == Event.id)
            .outerjoin(EventBatch, EventBatch.id == EventBatchItem.batch_id)
            .where(
                Event.run_id == run_id,
                Event.is_archived.is_(False),
            )
            .order_by(Event.sequence.asc(), Event.created_at.asc())
        )
        return [
            EventArchiveCandidate(
                id=event_id,
                event_type=event_type,
                workspace_id=workspace_id,
                run_id=row_run_id,
                sequence=sequence,
                created_at=created_at,
                batch_id=batch_id,
                batch_context_key=batch_context_key,
                batch_load_state=batch_load_state,
            )
            for (
                event_id,
                event_type,
                workspace_id,
                row_run_id,
                sequence,
                created_at,
                batch_id,
                batch_context_key,
                batch_load_state,
            ) in result.all()
        ]

    async def _load_workspace_event_candidates(
        self,
        conditions: Sequence[Any],
    ) -> list[EventArchiveCandidate]:
        result = await self.db.execute(
            select(
                Event.id,
                Event.event_type,
                Event.workspace_id,
                Event.run_id,
                Event.sequence,
                Event.created_at,
                EventBatch.id,
                EventBatch.context_key,
                EventBatch.load_state,
            )
            .outerjoin(EventBatchItem, EventBatchItem.event_id == Event.id)
            .outerjoin(EventBatch, EventBatch.id == EventBatchItem.batch_id)
            .where(and_(*conditions))
            .order_by(Event.created_at.asc(), Event.sequence.asc())
        )
        return [
            EventArchiveCandidate(
                id=event_id,
                event_type=event_type,
                workspace_id=workspace_id,
                run_id=row_run_id,
                sequence=sequence,
                created_at=created_at,
                batch_id=batch_id,
                batch_context_key=batch_context_key,
                batch_load_state=batch_load_state,
            )
            for (
                event_id,
                event_type,
                workspace_id,
                row_run_id,
                sequence,
                created_at,
                batch_id,
                batch_context_key,
                batch_load_state,
            ) in result.all()
        ]

    async def _load_full_events(
        self,
        candidates: Sequence[EventArchiveCandidate],
    ) -> list[Event]:
        event_ids = [candidate.id for candidate in candidates]
        if not event_ids:
            return []

        events_by_id: dict[UUID, Event] = {}
        for chunk in self._chunk_items(event_ids, DEFAULT_ARCHIVE_LOAD_CHUNK_SIZE):
            result = await self.db.execute(select(Event).where(Event.id.in_(chunk)))
            events_by_id.update({event.id: event for event in result.scalars().all()})
        return [
            events_by_id[event_id] for event_id in event_ids if event_id in events_by_id
        ]

    async def _create_archive_contexts(
        self,
        *,
        scope: str,
        scope_id: str,
        user_id: UUID,
        source_id: UUID,
        context_scope: ContextScope,
        workspace_id: UUID,
        run_id: UUID | None,
        events: Sequence[Event],
        reason: str,
        max_events_per_archive_context: int,
        max_chars_per_archive_context: int,
    ) -> list[tuple[list[Event], Context]]:
        chunks = self._split_event_chunks(
            events,
            max_events_per_archive_context=max_events_per_archive_context,
            max_chars_per_archive_context=max_chars_per_archive_context,
        )
        if not chunks:
            return []

        archive_id = uuid4()
        archived_chunks: list[tuple[list[Event], Context]] = []
        for chunk_index, chunk in enumerate(chunks):
            ctx = await self._create_archive_context(
                scope=scope,
                scope_id=scope_id,
                user_id=user_id,
                source_id=source_id,
                context_scope=context_scope,
                workspace_id=workspace_id,
                run_id=run_id,
                events=chunk,
                reason=reason,
                archive_id=archive_id,
                chunk_index=chunk_index,
                chunk_count=len(chunks),
            )
            archived_chunks.append((chunk, ctx))
        return archived_chunks

    async def _create_archive_context(
        self,
        *,
        scope: str,
        scope_id: str,
        user_id: UUID,
        source_id: UUID,
        context_scope: ContextScope,
        workspace_id: UUID,
        run_id: UUID | None,
        events: Sequence[Event],
        reason: str,
        archive_id: UUID,
        chunk_index: int,
        chunk_count: int,
    ) -> Context:
        now = datetime.now(UTC)
        path = self._archive_path(
            scope,
            scope_id,
            archive_id,
            now,
            chunk_index=chunk_index,
            chunk_count=chunk_count,
        )
        first = events[0]
        last = events[-1]
        type_counts = Counter(_event_type_value(event.event_type) for event in events)
        batch_meta = await self._batch_meta_for_events([event.id for event in events])
        lines = [
            "# Event Archive",
            "",
            f"Scope: {scope}",
            f"Scope ID: {scope_id}",
            f"Archived at: {now.isoformat()}",
            f"Reason: {reason}",
            f"Events: {len(events)}",
        ]
        if chunk_count > 1:
            lines.append(f"Chunk: {chunk_index + 1}/{chunk_count}")
        lines.extend(
            [
                "",
                "## Timeline",
            ]
        )
        for event in events:
            created_at = event.created_at.isoformat() if event.created_at else ""
            lines.append(
                f"[{event.sequence:03d}] {created_at} "
                f"{_event_type_value(event.event_type)} - {_event_context_line(event)}"
            )

        ctx = Context(
            user_id=user_id,
            source_id=source_id,
            scope=context_scope,
            context_type=ContextType.EVENT_ARCHIVE,
            path=path,
            glance=self._archive_glance(scope, len(events), chunk_index, chunk_count),
            content="\n".join(lines),
            tags=["events", "archive", scope],
            meta={
                "archive_id": str(archive_id),
                "archive_scope": scope,
                "archive_reason": reason,
                "archive_chunk_index": chunk_index + 1,
                "archive_chunk_count": chunk_count,
                "workspace_id": str(workspace_id),
                "run_id": str(run_id) if run_id else None,
                "event_count": len(events),
                "event_types": dict(type_counts),
                "batch_ids": batch_meta["batch_ids"],
                "batch_context_keys": batch_meta["batch_context_keys"],
                "first_event_id": str(first.id),
                "last_event_id": str(last.id),
                "first_sequence": first.sequence,
                "last_sequence": last.sequence,
                "created_at": now.isoformat(),
            },
        )
        self.db.add(ctx)
        await self.db.flush()
        return ctx

    async def _mark_archived_bulk(
        self,
        event_ids: Sequence[UUID],
        archive_context: Context,
        scope: str,
        reason: str,
        *,
        chunk_size: int,
    ) -> None:
        archived_at = datetime.now(UTC)
        for chunk in self._chunk_items(event_ids, max(1, int(chunk_size))):
            stmt = (
                update(Event)
                .where(Event.id.in_(chunk))
                .values(
                    is_archived=True,
                    archived_at=archived_at,
                    archive_scope=scope,
                    archive_reason=reason,
                    archive_context_id=archive_context.id,
                )
                .execution_options(synchronize_session=False)
            )
            await self.db.execute(stmt)
            batch_ids = await self._fully_covered_batch_ids(chunk)
            if batch_ids:
                await self.db.execute(
                    update(EventBatch)
                    .where(EventBatch.id.in_(batch_ids))
                    .values(
                        state=ContextBatchState.ARCHIVED.value,
                        archive_context_id=archive_context.id,
                        updated_at=archived_at,
                    )
                    .execution_options(synchronize_session=False)
                )

    async def _fully_covered_batch_ids(self, event_ids: Sequence[UUID]) -> list[UUID]:
        if not event_ids:
            return []
        event_id_set = set(event_ids)
        touched = await self.db.execute(
            select(EventBatchItem.batch_id)
            .where(EventBatchItem.event_id.in_(event_ids))
            .distinct()
        )
        touched_batch_ids = [batch_id for (batch_id,) in touched.all()]
        if not touched_batch_ids:
            return []

        result = await self.db.execute(
            select(EventBatchItem.batch_id, EventBatchItem.event_id).where(
                EventBatchItem.batch_id.in_(touched_batch_ids)
            )
        )
        batch_event_ids: dict[UUID, set[UUID]] = {}
        for batch_id, event_id in result.all():
            batch_event_ids.setdefault(batch_id, set()).add(event_id)
        return [
            batch_id
            for batch_id, batch_events in batch_event_ids.items()
            if batch_events.issubset(event_id_set)
        ]

    async def _batch_meta_for_events(self, event_ids: Sequence[UUID]) -> dict[str, Any]:
        if not event_ids:
            return {"batch_ids": [], "batch_context_keys": []}
        result = await self.db.execute(
            select(EventBatch.id, EventBatch.context_key)
            .join(EventBatchItem, EventBatchItem.batch_id == EventBatch.id)
            .where(EventBatchItem.event_id.in_(event_ids))
            .distinct()
            .order_by(EventBatch.context_key.asc())
        )
        rows = result.all()
        return {
            "batch_ids": [str(batch_id) for batch_id, _ in rows],
            "batch_context_keys": [context_key for _, context_key in rows],
        }

    @staticmethod
    def _split_event_chunks(
        events: Sequence[Event],
        *,
        max_events_per_archive_context: int,
        max_chars_per_archive_context: int,
    ) -> list[list[Event]]:
        max_events = max(1, int(max_events_per_archive_context))
        max_chars = max(1, int(max_chars_per_archive_context))
        chunks: list[list[Event]] = []
        current: list[Event] = []
        current_chars = 0

        for event in events:
            line_size = EventArchiveService._archive_line_size(event)
            if current and (
                len(current) >= max_events or current_chars + line_size > max_chars
            ):
                chunks.append(current)
                current = []
                current_chars = 0
            current.append(event)
            current_chars += line_size

        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _archive_line_size(event: Event) -> int:
        created_at = event.created_at.isoformat() if event.created_at else ""
        return (
            len(str(event.sequence))
            + len(created_at)
            + len(_event_type_value(event.event_type))
            + len(_event_context_line(event))
            + 16
        )

    @staticmethod
    def _archive_glance(
        scope: str,
        event_count: int,
        chunk_index: int,
        chunk_count: int,
    ) -> str:
        if chunk_count <= 1:
            return f"Archived {event_count} {scope} event(s)"
        return (
            f"Archived {event_count} {scope} event(s) "
            f"(part {chunk_index + 1}/{chunk_count})"
        )

    @staticmethod
    def _chunk_items(items: Sequence[Any], chunk_size: int) -> list[Sequence[Any]]:
        size = max(1, int(chunk_size))
        return [items[index : index + size] for index in range(0, len(items), size)]

    @staticmethod
    def _archive_path(
        scope: str,
        scope_id: str,
        archive_id: UUID,
        archived_at: datetime,
        *,
        chunk_index: int = 0,
        chunk_count: int = 1,
    ) -> str:
        stamp = archived_at.strftime("%Y%m%d%H%M%S")
        suffix = f"-part-{chunk_index + 1:03d}" if chunk_count > 1 else ""
        if scope == "run":
            return (
                f"archives/runs/{scope_id[:8]}/events/"
                f"{stamp}-{str(archive_id)[:8]}{suffix}"
            )
        return f"archives/workspace/events/{stamp}-{str(archive_id)[:8]}{suffix}"

    @staticmethod
    def _result(
        *,
        scope: str,
        scope_id: str,
        dry_run: bool,
        active_count_before: int,
        candidates: Sequence[Any],
        archive_contexts: Sequence[Context] | None,
        reason: str,
        strategy: str,
    ) -> EventArchiveResult:
        contexts = list(archive_contexts or [])
        archive_context = contexts[0] if contexts else None
        return EventArchiveResult(
            scope=scope,
            scope_id=scope_id,
            dry_run=dry_run,
            archived_count=len(candidates),
            skipped_count=active_count_before - len(candidates),
            active_count_before=active_count_before,
            archive_context_id=str(archive_context.id) if archive_context else None,
            archive_path=archive_context.path if archive_context else None,
            reason=reason,
            strategy=strategy,
            archive_context_ids=[str(context.id) for context in contexts],
            archive_paths=[context.path for context in contexts],
            archive_chunks=len(contexts),
        )


__all__ = [
    "EventArchiveResult",
    "EventArchiveService",
    "select_archive_candidates",
]
