"""Archive active event memory without deleting the audit log."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.enums import EventType
from structure.core.enums.context import ContextScope, ContextType
from structure.models.context.context import Context
from structure.models.events.event import Event
from structure.services.events.event_gc import (
    DEFAULT_EVENT_GC_STRATEGY,
    EventGCStrategyRegistry,
)
from structure.utils.workspace_context_cache import invalidate_workspace_context_cache


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
    ) -> EventArchiveResult:
        run_uuid = run_id if isinstance(run_id, UUID) else UUID(str(run_id))
        user_uuid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))

        result = await self.db.execute(
            select(Event)
            .where(
                Event.run_id == run_uuid,
                Event.is_archived.is_(False),
            )
            .order_by(Event.sequence.asc(), Event.created_at.asc())
        )
        events = list(result.scalars().all())
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
                archive_context=None,
                reason=reason,
                strategy=strategy,
            )

        workspace_id = candidates[0].workspace_id
        workspace_uuid = UUID(str(workspace_id))
        ctx = await self._create_archive_context(
            scope="run",
            scope_id=str(run_uuid),
            user_id=user_uuid,
            source_id=workspace_uuid,
            context_scope=ContextScope.WORKSPACE,
            workspace_id=workspace_uuid,
            run_id=run_uuid,
            events=candidates,
            reason=reason,
        )
        await self._mark_archived(candidates, ctx, "run", reason)
        await self.db.flush()
        if workspace_id:
            invalidate_workspace_context_cache(str(workspace_id))
        return self._result(
            scope="run",
            scope_id=str(run_uuid),
            dry_run=False,
            active_count_before=len(events),
            candidates=candidates,
            archive_context=ctx,
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

        result = await self.db.execute(
            select(Event)
            .where(and_(*conditions))
            .order_by(Event.created_at.asc(), Event.sequence.asc())
        )
        events = list(result.scalars().all())
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
                archive_context=None,
                reason=reason,
                strategy=strategy,
            )

        ctx = await self._create_archive_context(
            scope="workspace",
            scope_id=str(workspace_uuid),
            user_id=user_uuid,
            source_id=workspace_uuid,
            context_scope=ContextScope.WORKSPACE,
            workspace_id=workspace_uuid,
            run_id=None,
            events=candidates,
            reason=reason,
        )
        await self._mark_archived(candidates, ctx, "workspace", reason)
        await self.db.flush()
        invalidate_workspace_context_cache(str(workspace_uuid))
        return self._result(
            scope="workspace",
            scope_id=str(workspace_uuid),
            dry_run=False,
            active_count_before=len(events),
            candidates=candidates,
            archive_context=ctx,
            reason=reason,
            strategy=strategy,
        )

    @staticmethod
    def select_candidates(
        events: Sequence[Event],
        *,
        scope: str,
        strategy: str,
        strategy_config: Mapping[str, Any] | None,
        keep_last: int | None,
        include_pinned: bool,
        event_types: Iterable[str] | None,
    ) -> list[Event]:
        cfg = dict(strategy_config or {})
        cfg.setdefault("include_pinned", include_pinned)
        if event_types is not None:
            cfg.setdefault("event_types", list(event_types))
        if keep_last is not None:
            cfg.setdefault("keep_last_floor", keep_last)

        gc_strategy = EventGCStrategyRegistry.get(strategy)
        return list(gc_strategy.select_candidates(events, scope=scope, config=cfg))

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
    ) -> Context:
        now = datetime.now(UTC)
        archive_id = uuid4()
        path = self._archive_path(scope, scope_id, archive_id, now)
        first = events[0]
        last = events[-1]
        type_counts = Counter(_event_type_value(event.event_type) for event in events)
        lines = [
            "# Event Archive",
            "",
            f"Scope: {scope}",
            f"Scope ID: {scope_id}",
            f"Archived at: {now.isoformat()}",
            f"Reason: {reason}",
            f"Events: {len(events)}",
            "",
            "## Timeline",
        ]
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
            glance=f"Archived {len(events)} {scope} event(s)",
            content="\n".join(lines),
            tags=["events", "archive", scope],
            meta={
                "archive_id": str(archive_id),
                "archive_scope": scope,
                "archive_reason": reason,
                "workspace_id": str(workspace_id),
                "run_id": str(run_id) if run_id else None,
                "event_count": len(events),
                "event_types": dict(type_counts),
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

    async def _mark_archived(
        self,
        events: Sequence[Event],
        archive_context: Context,
        scope: str,
        reason: str,
    ) -> None:
        archived_at = datetime.now(UTC)
        for event in events:
            event.is_archived = True
            event.archived_at = archived_at
            event.archive_scope = scope
            event.archive_reason = reason
            event.archive_context_id = archive_context.id

    @staticmethod
    def _archive_path(
        scope: str,
        scope_id: str,
        archive_id: UUID,
        archived_at: datetime,
    ) -> str:
        stamp = archived_at.strftime("%Y%m%d%H%M%S")
        if scope == "run":
            return f"archives/runs/{scope_id[:8]}/events/{stamp}-{str(archive_id)[:8]}"
        return f"archives/workspace/events/{stamp}-{str(archive_id)[:8]}"

    @staticmethod
    def _result(
        *,
        scope: str,
        scope_id: str,
        dry_run: bool,
        active_count_before: int,
        candidates: Sequence[Event],
        archive_context: Context | None,
        reason: str,
        strategy: str,
    ) -> EventArchiveResult:
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
        )


__all__ = [
    "EventArchiveResult",
    "EventArchiveService",
    "select_archive_candidates",
]
