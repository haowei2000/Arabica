"""Integration tests for Event GC archive behavior.

These tests use a real PostgreSQL database because archive GC crosses ORM
models, bulk updates, Context writes, and EventCRUD active-memory filters.
Set ``STRUCTURE_INTEGRATION_DATABASE_URL`` to run against a dedicated test DB.
If unset, the tests read the local ``.env`` Docker database settings, then fall
back to the repository's conventional local test database and skip cleanly when
neither is available.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from urllib.parse import quote_plus
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from structure.core.enums import EventType
from structure.core.enums.context import ContextType
from structure.models.app.app import App
from structure.models.context.context import Context
from structure.models.events.event import Event
from structure.models.runs.artifact import Artifact
from structure.models.runs.run import Run
from structure.models.runs.task import Task
from structure.models.workspaces.workspace import Workspace
from structure.models.workspaces.workspace_member import WorkspaceMember
from structure.services.events.event_archive import EventArchiveService
from structure.services.events.event_crud import EventCRUD

pytestmark = pytest.mark.integration


_DEFAULT_DB_URL = (
    "postgresql+asyncpg://test_user:test_password@localhost:5432/structure_test"
)


def _project_env_values() -> dict[str, str]:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _integration_db_url() -> str:
    if explicit_url := os.getenv("STRUCTURE_INTEGRATION_DATABASE_URL"):
        return explicit_url

    project_env = _project_env_values()
    if not project_env:
        return _DEFAULT_DB_URL

    username = quote_plus(project_env.get("POSTGRES__USERNAME", "test_user"))
    password = quote_plus(project_env.get("POSTGRES__PASSWORD", "test_password"))
    host = project_env.get("POSTGRES__HOST", "localhost")
    port = project_env.get(
        "POSTGRES__PORT",
        project_env.get("EXPOSE_PORT_POSTGRES", "5432"),
    )
    database = project_env.get("POSTGRES__STRUCTURE_DBNAME", "structure_test")
    return f"postgresql+asyncpg://{username}:{password}@{host}:{port}/{database}"


@pytest.fixture()
async def event_gc_sessionmaker() -> AsyncGenerator[
    async_sessionmaker[AsyncSession],
    None,
]:
    db_url = _integration_db_url()
    schema = f"event_gc_it_{uuid4().hex}"
    bootstrap_engine = create_async_engine(db_url, pool_pre_ping=False)

    try:
        async with bootstrap_engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:
        await bootstrap_engine.dispose()
        pytest.skip(f"PostgreSQL integration database is unavailable: {exc}")

    raw_engine = create_async_engine(db_url, pool_pre_ping=False)
    engine = raw_engine.execution_options(schema_translate_map={None: schema})
    tables = [
        App.__table__,
        Workspace.__table__,
        Run.__table__,
        Artifact.__table__,
        Task.__table__,
        Context.__table__,
        Event.__table__,
        WorkspaceMember.__table__,
    ]

    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Event.metadata.create_all(
                    sync_conn,
                    tables=tables,
                )
            )

        yield async_sessionmaker(
            engine,
            expire_on_commit=False,
            autoflush=False,
        )
    finally:
        await raw_engine.dispose()
        async with bootstrap_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await bootstrap_engine.dispose()


async def _seed_run_events(
    session: AsyncSession,
) -> tuple[UUID, UUID, UUID]:
    user_id = uuid4()
    workspace_id = uuid4()
    run_id = uuid4()

    session.add(
        Workspace(
            id=workspace_id,
            name="Event GC integration workspace",
            owner_id=user_id,
            settings={},
        )
    )
    session.add(
        Run(
            id=run_id,
            workspace_id=workspace_id,
            user_id=user_id,
            input_data={"message": "start"},
            output_data={},
            waiting_for={},
        )
    )

    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    events = [
        (EventType.USER_MESSAGE, {"message": "remember this"}, 1),
        (EventType.AGENT_TOKEN, {"token": "noise"}, 2),
        (EventType.AGENT_THINKING, {"content": "scratchpad"}, 3),
        (EventType.TOOL_CALL, {"tool_name": "echo", "arguments": {"text": "hi"}}, 4),
        (EventType.TOOL_RESULT, {"tool_name": "echo", "result": "hi"}, 5),
        (EventType.AGENT_MESSAGE, {"message": "done"}, 6),
    ]
    session.add_all(
        Event(
            event_type=event_type.value,
            workspace_id=workspace_id,
            run_id=run_id,
            user_id=user_id,
            payload=payload,
            sequence=sequence,
            created_at=base_time + timedelta(seconds=sequence),
            is_archived=False,
        )
        for event_type, payload, sequence in events
    )
    await session.commit()
    return user_id, workspace_id, run_id


async def test_event_gc_run_archive_round_trip_with_context_chunks(
    event_gc_sessionmaker: async_sessionmaker[AsyncSession],
):
    async with event_gc_sessionmaker() as session:
        user_id, _workspace_id, run_id = await _seed_run_events(session)
        service = EventArchiveService(session)

        dry_run = await service.archive_run_memory(
            run_id,
            user_id=user_id,
            keep_last=0,
            include_pinned=False,
            dry_run=True,
            max_events_per_archive_context=2,
        )

        assert dry_run.archived_count == 3
        assert dry_run.archive_chunks == 0
        assert await session.scalar(select(func.count(Context.id))) == 0
        assert (
            await session.scalar(
                select(func.count(Event.id)).where(Event.is_archived.is_(True))
            )
            == 0
        )

        result = await service.archive_run_memory(
            run_id,
            user_id=user_id,
            keep_last=0,
            include_pinned=False,
            reason="integration_event_gc",
            max_events_per_archive_context=2,
        )
        await session.commit()

        assert result.archived_count == 3
        assert result.archive_chunks == 2
        assert len(result.archive_context_ids) == 2
        assert all(path is not None for path in result.archive_paths)

        active_events, active_total = await EventCRUD(session).list_by_run(
            run_id,
            include_archived=False,
        )
        all_events, all_total = await EventCRUD(session).list_by_run(
            run_id,
            include_archived=True,
        )
        archived_events = (
            (
                await session.execute(
                    select(Event)
                    .where(Event.run_id == run_id, Event.is_archived.is_(True))
                    .order_by(Event.sequence.asc())
                )
            )
            .scalars()
            .all()
        )
        archive_contexts = (
            (
                await session.execute(
                    select(Context)
                    .where(Context.context_type == ContextType.EVENT_ARCHIVE.value)
                    .order_by(Context.path.asc())
                )
            )
            .scalars()
            .all()
        )

        assert [event.sequence for event in active_events] == [1, 5, 6]
        assert active_total == 3
        assert [event.sequence for event in all_events] == [1, 2, 3, 4, 5, 6]
        assert all_total == 6
        assert [event.sequence for event in archived_events] == [2, 3, 4]
        assert {event.archive_reason for event in archived_events} == {
            "integration_event_gc"
        }
        assert len(archive_contexts) == 2
        assert {
            context.meta["archive_chunk_count"] for context in archive_contexts
        } == {2}


async def test_event_gc_workspace_archive_can_exclude_run_events(
    event_gc_sessionmaker: async_sessionmaker[AsyncSession],
):
    async with event_gc_sessionmaker() as session:
        user_id, workspace_id, run_id = await _seed_run_events(session)
        session.add(
            Event(
                event_type=EventType.AGENT_TOKEN.value,
                workspace_id=workspace_id,
                run_id=None,
                user_id=user_id,
                payload={"token": "workspace-noise"},
                sequence=7,
                created_at=datetime(2026, 1, 1, 0, 0, 7, tzinfo=UTC),
                is_archived=False,
            )
        )
        session.add(
            Event(
                event_type=EventType.AGENT_MESSAGE.value,
                workspace_id=workspace_id,
                run_id=None,
                user_id=user_id,
                payload={"message": "workspace follow-up"},
                sequence=8,
                created_at=datetime(2026, 1, 1, 0, 0, 8, tzinfo=UTC),
                is_archived=False,
            )
        )
        await session.commit()

        result = await EventArchiveService(session).archive_workspace_memory(
            workspace_id,
            user_id=user_id,
            keep_last=0,
            include_run_events=False,
            event_types=[EventType.AGENT_TOKEN.value],
            reason="workspace_only_gc",
        )
        await session.commit()

        run_archived_count = await session.scalar(
            select(func.count(Event.id)).where(
                Event.run_id == run_id,
                Event.is_archived.is_(True),
            )
        )
        workspace_archived = (
            (
                await session.execute(
                    select(Event).where(
                        Event.workspace_id == workspace_id,
                        Event.run_id.is_(None),
                        Event.is_archived.is_(True),
                    )
                )
            )
            .scalars()
            .one()
        )

        assert result.archived_count == 1
        assert run_archived_count == 0
        assert workspace_archived.sequence == 7
        assert workspace_archived.archive_scope == "workspace"
