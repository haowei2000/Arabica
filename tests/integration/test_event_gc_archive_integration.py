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
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import re
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
from structure.models.events.event_batch import EventBatch, EventBatchItem
from structure.models.runs.artifact import Artifact
from structure.models.runs.run import Run
from structure.models.runs.task import Task
from structure.models.workspaces.workspace import Workspace
from structure.models.workspaces.workspace_member import WorkspaceMember
from structure.plugins.executors.default.concrete import DefaultExecutor
from structure.services.events.context_batch_service import ContextBatchService
from structure.services.events.event_archive import EventArchiveService
from structure.services.events.event_crud import EventCRUD

pytestmark = pytest.mark.integration


_DEFAULT_DB_URL = (
    "postgresql+asyncpg://test_user:test_password@localhost:5432/structure_test"
)
_TOKEN_RE = re.compile(r"\w+|[^\s\w]")


@pytest.fixture(autouse=True)
def openai_env_contract(monkeypatch):
    monkeypatch.setenv("OPENAI__API_KEY", "test-key")
    monkeypatch.setenv("OPENAI__BASE_URL", "http://example.test/v1")
    monkeypatch.setenv("OPENAI__MODEL", "test-model")
    from structure.config.factory import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@dataclass(frozen=True)
class DialogueEventSpec:
    event_type: EventType
    payload: dict
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class DialogueTaskSpec:
    name: str
    events: tuple[DialogueEventSpec, ...]


@dataclass(frozen=True)
class DialogueTaskMetrics:
    consumed_tokens: int
    cached_tokens: int
    completion_event_count: int


@dataclass(frozen=True)
class OpenSourceBenchmarkSpec:
    name: str
    upstream_url: str
    primary_signal: str
    integration_strategy: str
    local_scenarios: tuple[str, ...]


_OPEN_SOURCE_AGENT_BENCHMARKS = (
    OpenSourceBenchmarkSpec(
        name="tau-bench",
        upstream_url="https://github.com/sierra-research/tau-bench",
        primary_signal="multi-turn user + tool interaction with final state checks",
        integration_strategy="distill final-state tool loops into deterministic traces",
        local_scenarios=("tau_retail_exchange",),
    ),
    OpenSourceBenchmarkSpec(
        name="BFCL",
        upstream_url="https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard",
        primary_signal="single, parallel, and multi-turn function-call correctness",
        integration_strategy="distill function-call transcripts and assert tool-call order",
        local_scenarios=("bfcl_parallel_tools",),
    ),
    OpenSourceBenchmarkSpec(
        name="AgentBench",
        upstream_url="https://github.com/THUDM/AgentBench",
        primary_signal="programmatically scored tasks across OS, DB, web, games, and KG",
        integration_strategy="distill OS/DB-style executable verifier traces",
        local_scenarios=("terminal_verifier",),
    ),
    OpenSourceBenchmarkSpec(
        name="Terminal-Bench",
        upstream_url="https://github.com/laude-institute/terminal-bench",
        primary_signal="terminal task completion verified by executable tests",
        integration_strategy="distill command + verifier event traces",
        local_scenarios=("terminal_verifier",),
    ),
    OpenSourceBenchmarkSpec(
        name="SWE-bench",
        upstream_url="https://github.com/swe-bench/SWE-bench",
        primary_signal="real GitHub issue resolution validated by regression tests",
        integration_strategy="too heavy for normal CI; keep as external benchmark adapter",
        local_scenarios=(),
    ),
    OpenSourceBenchmarkSpec(
        name="WebArena/OSWorld",
        upstream_url="https://github.com/web-arena-x/webarena",
        primary_signal="browser or desktop workflows in realistic interactive environments",
        integration_strategy="too heavy for normal CI; reserve for optional e2e jobs",
        local_scenarios=(),
    ),
)

_DIALOGUE_TASKS = (
    DialogueTaskSpec(
        name="baseline_summary",
        events=(
            DialogueEventSpec(
                EventType.USER_MESSAGE,
                {"message": "Summarize the project stability budget."},
            ),
            DialogueEventSpec(
                EventType.AGENT_MESSAGE,
                {"content": "The stability budget favors deterministic prompts."},
                input_tokens=110,
                output_tokens=18,
            ),
        ),
    ),
    DialogueTaskSpec(
        name="context_lookup",
        events=(
            DialogueEventSpec(
                EventType.USER_MESSAGE,
                {"message": "Read the tool policy context and report the rule."},
            ),
            DialogueEventSpec(
                EventType.AGENT_MESSAGE,
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-read-policy",
                            "name": "read_context",
                            "arguments": {"path": "/knowledge/tool-policy"},
                        }
                    ],
                },
                input_tokens=155,
                output_tokens=12,
            ),
            DialogueEventSpec(
                EventType.TOOL_CALL,
                {
                    "tool_name": "read_context",
                    "tool_id": "call-read-policy",
                    "arguments": {"path": "/knowledge/tool-policy"},
                },
            ),
            DialogueEventSpec(
                EventType.TOOL_RESULT,
                {
                    "tool_name": "read_context",
                    "tool_id": "call-read-policy",
                    "result": {
                        "data": {
                            "path": "/knowledge/tool-policy",
                            "content": "Read descriptions before loading schemas.",
                        }
                    },
                },
            ),
            DialogueEventSpec(
                EventType.AGENT_TOKEN,
                {"token": "stream-noise-that-should-not-enter-prompts"},
            ),
            DialogueEventSpec(
                EventType.AGENT_MESSAGE,
                {"content": "The rule is to read descriptions before schemas."},
                input_tokens=188,
                output_tokens=24,
            ),
        ),
    ),
    DialogueTaskSpec(
        name="calculator_followup",
        events=(
            DialogueEventSpec(
                EventType.USER_MESSAGE,
                {
                    "message": (
                        "Using the prior stability rule, calculate 21 * 2 with "
                        "the calculator."
                    )
                },
            ),
            DialogueEventSpec(
                EventType.AGENT_MESSAGE,
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-calc",
                            "name": "calculator",
                            "arguments": {"expression": "21 * 2"},
                        }
                    ],
                },
                input_tokens=170,
                output_tokens=9,
            ),
            DialogueEventSpec(
                EventType.TOOL_CALL,
                {
                    "tool_name": "calculator",
                    "tool_id": "call-calc",
                    "arguments": {"expression": "21 * 2"},
                },
            ),
            DialogueEventSpec(
                EventType.TOOL_RESULT,
                {
                    "tool_name": "calculator",
                    "tool_id": "call-calc",
                    "result": {"value": 42},
                },
            ),
            DialogueEventSpec(
                EventType.AGENT_MESSAGE,
                {"content": "21 * 2 is 42."},
                input_tokens=175,
                output_tokens=16,
            ),
        ),
    ),
)

_AGENT_BENCHMARK_DIALOGUE_TASKS = (
    DialogueTaskSpec(
        name="tau_retail_exchange",
        events=(
            DialogueEventSpec(
                EventType.USER_MESSAGE,
                {
                    "message": (
                        "Exchange order O-100 item SKU-RED for SKU-BLUE if policy "
                        "allows it."
                    )
                },
            ),
            DialogueEventSpec(
                EventType.AGENT_MESSAGE,
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-order",
                            "name": "lookup_order",
                            "arguments": {"order_id": "O-100"},
                        }
                    ],
                },
                input_tokens=220,
                output_tokens=16,
            ),
            DialogueEventSpec(
                EventType.TOOL_CALL,
                {
                    "tool_name": "lookup_order",
                    "tool_id": "call-order",
                    "arguments": {"order_id": "O-100"},
                },
            ),
            DialogueEventSpec(
                EventType.TOOL_RESULT,
                {
                    "tool_name": "lookup_order",
                    "tool_id": "call-order",
                    "result": {
                        "status": "delivered",
                        "days_since_delivery": 12,
                        "items": ["SKU-RED"],
                    },
                },
            ),
            DialogueEventSpec(
                EventType.USER_FEEDBACK,
                {
                    "feedback": "I confirm the single-item exchange.",
                },
            ),
            DialogueEventSpec(
                EventType.AGENT_MESSAGE,
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-exchange",
                            "name": "create_exchange",
                            "arguments": {
                                "order_id": "O-100",
                                "from_sku": "SKU-RED",
                                "to_sku": "SKU-BLUE",
                            },
                        }
                    ],
                },
                input_tokens=260,
                output_tokens=15,
            ),
            DialogueEventSpec(
                EventType.TOOL_CALL,
                {
                    "tool_name": "create_exchange",
                    "tool_id": "call-exchange",
                    "arguments": {
                        "order_id": "O-100",
                        "from_sku": "SKU-RED",
                        "to_sku": "SKU-BLUE",
                    },
                },
            ),
            DialogueEventSpec(
                EventType.TOOL_RESULT,
                {
                    "tool_name": "create_exchange",
                    "tool_id": "call-exchange",
                    "result": {
                        "exchange_id": "EX-9",
                        "state": "created",
                    },
                },
            ),
            DialogueEventSpec(
                EventType.AGENT_MESSAGE,
                {
                    "content": "Exchange EX-9 was created for SKU-BLUE.",
                    "final_state": {"exchange_state": "created"},
                },
                input_tokens=240,
                output_tokens=22,
            ),
        ),
    ),
    DialogueTaskSpec(
        name="bfcl_parallel_tools",
        events=(
            DialogueEventSpec(
                EventType.USER_MESSAGE,
                {
                    "message": (
                        "Fetch customer C-7 and invoice I-9 in parallel, then "
                        "combine the answer."
                    )
                },
            ),
            DialogueEventSpec(
                EventType.AGENT_MESSAGE,
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-customer",
                            "name": "get_customer",
                            "arguments": {"customer_id": "C-7"},
                        },
                        {
                            "id": "call-invoice",
                            "name": "get_invoice",
                            "arguments": {"invoice_id": "I-9"},
                        },
                    ],
                },
                input_tokens=190,
                output_tokens=18,
            ),
            DialogueEventSpec(
                EventType.TOOL_CALL,
                {
                    "tool_name": "get_customer",
                    "tool_id": "call-customer",
                    "arguments": {"customer_id": "C-7"},
                },
            ),
            DialogueEventSpec(
                EventType.TOOL_CALL,
                {
                    "tool_name": "get_invoice",
                    "tool_id": "call-invoice",
                    "arguments": {"invoice_id": "I-9"},
                },
            ),
            DialogueEventSpec(
                EventType.TOOL_RESULT,
                {
                    "tool_name": "get_customer",
                    "tool_id": "call-customer",
                    "result": {"name": "Ada", "tier": "enterprise"},
                },
            ),
            DialogueEventSpec(
                EventType.TOOL_RESULT,
                {
                    "tool_name": "get_invoice",
                    "tool_id": "call-invoice",
                    "result": {"amount": 42, "currency": "USD"},
                },
            ),
            DialogueEventSpec(
                EventType.AGENT_MESSAGE,
                {"content": "Ada has a 42 USD enterprise invoice."},
                input_tokens=205,
                output_tokens=20,
            ),
        ),
    ),
    DialogueTaskSpec(
        name="terminal_verifier",
        events=(
            DialogueEventSpec(
                EventType.USER_MESSAGE,
                {
                    "message": (
                        "Create a config file and run the verifier until it passes."
                    )
                },
            ),
            DialogueEventSpec(
                EventType.AGENT_MESSAGE,
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-write-config",
                            "name": "write_file",
                            "arguments": {
                                "path": "/tmp/app.cfg",
                                "content": "mode=batch\n",
                            },
                        }
                    ],
                },
                input_tokens=210,
                output_tokens=12,
            ),
            DialogueEventSpec(
                EventType.TOOL_CALL,
                {
                    "tool_name": "write_file",
                    "tool_id": "call-write-config",
                    "arguments": {
                        "path": "/tmp/app.cfg",
                        "content": "mode=batch\n",
                    },
                },
            ),
            DialogueEventSpec(
                EventType.TOOL_RESULT,
                {
                    "tool_name": "write_file",
                    "tool_id": "call-write-config",
                    "result": {"ok": True},
                },
            ),
            DialogueEventSpec(
                EventType.AGENT_MESSAGE,
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-pytest",
                            "name": "run_shell",
                            "arguments": {"command": "pytest -q verifier"},
                        }
                    ],
                },
                input_tokens=230,
                output_tokens=10,
            ),
            DialogueEventSpec(
                EventType.TOOL_CALL,
                {
                    "tool_name": "run_shell",
                    "tool_id": "call-pytest",
                    "arguments": {"command": "pytest -q verifier"},
                },
            ),
            DialogueEventSpec(
                EventType.TOOL_RESULT,
                {
                    "tool_name": "run_shell",
                    "tool_id": "call-pytest",
                    "result": {"exit_code": 0, "stdout": "1 passed"},
                },
            ),
            DialogueEventSpec(
                EventType.AGENT_MESSAGE,
                {
                    "content": "Verifier passed after creating /tmp/app.cfg.",
                    "final_state": {"verifier_exit_code": 0},
                },
                input_tokens=245,
                output_tokens=18,
            ),
        ),
    ),
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
        EventBatch.__table__,
        EventBatchItem.__table__,
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


def _render_prompt(messages) -> str:
    rendered = []
    for message in messages:
        rendered.append(
            json.dumps(
                {
                    "role": message.role,
                    "content": message.content,
                    "tool_call_id": getattr(message, "tool_call_id", None),
                    "tool_calls": [
                        _tool_call_to_dict(call)
                        for call in (getattr(message, "tool_calls", None) or [])
                    ],
                },
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            )
        )
    return "\n".join(rendered)


def _tool_call_to_dict(call) -> dict:
    if hasattr(call, "model_dump"):
        return call.model_dump(mode="json", exclude_none=True)
    if hasattr(call, "__dataclass_fields__"):
        return asdict(call)
    return {
        "id": getattr(call, "id", None),
        "name": getattr(call, "name", None),
        "arguments": getattr(call, "arguments", {}),
    }


def _estimated_token_count(text: str) -> int:
    return len(_TOKEN_RE.findall(text))


def _common_prefix(left: str, right: str) -> str:
    index = 0
    max_index = min(len(left), len(right))
    while index < max_index and left[index] == right[index]:
        index += 1
    return left[:index]


async def _seed_dialogue_task_specs(
    session: AsyncSession,
    tasks: tuple[DialogueTaskSpec, ...],
    *,
    omit_final_task_names: frozenset[str] = frozenset(),
) -> tuple[UUID, UUID, dict[str, UUID], list[Event]]:
    user_id = uuid4()
    workspace_id = uuid4()
    run_ids: dict[str, UUID] = {}

    session.add(
        Workspace(
            id=workspace_id,
            name="Context batch metrics integration workspace",
            owner_id=user_id,
            settings={},
        )
    )

    created_events: list[Event] = []
    base_time = datetime(2026, 1, 2, tzinfo=UTC)
    sequence = 1
    for task in tasks:
        run_id = uuid4()
        run_ids[task.name] = run_id
        session.add(
            Run(
                id=run_id,
                workspace_id=workspace_id,
                user_id=user_id,
                input_data={"task": task.name},
                output_data={},
                waiting_for={},
            )
        )

        specs = task.events
        if task.name in omit_final_task_names:
            specs = task.events[:-1]

        for spec in specs:
            event = Event(
                event_type=spec.event_type.value,
                workspace_id=workspace_id,
                run_id=run_id,
                user_id=user_id,
                payload=spec.payload,
                sequence=sequence,
                created_at=base_time + timedelta(seconds=sequence),
                input_tokens=spec.input_tokens,
                output_tokens=spec.output_tokens,
                is_archived=False,
            )
            session.add(event)
            created_events.append(event)
            sequence += 1

    await session.flush()
    await ContextBatchService(session).assign_events_to_batches(
        created_events,
        apply_policy=True,
    )
    await session.commit()
    return user_id, workspace_id, run_ids, created_events


async def _seed_dialogue_tasks(
    session: AsyncSession,
) -> tuple[UUID, UUID, dict[str, UUID], list[Event]]:
    return await _seed_dialogue_task_specs(
        session,
        _DIALOGUE_TASKS,
        omit_final_task_names=frozenset({"calculator_followup"}),
    )


async def _append_final_calculator_answer(
    session: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    run_id: UUID,
) -> Event:
    max_sequence = await session.scalar(
        select(func.max(Event.sequence)).where(Event.workspace_id == workspace_id)
    )
    final_spec = _DIALOGUE_TASKS[-1].events[-1]
    sequence = int(max_sequence or 0) + 1
    event = Event(
        event_type=final_spec.event_type.value,
        workspace_id=workspace_id,
        run_id=run_id,
        user_id=user_id,
        payload=final_spec.payload,
        sequence=sequence,
        created_at=datetime(2026, 1, 2, tzinfo=UTC) + timedelta(seconds=sequence),
        input_tokens=final_spec.input_tokens,
        output_tokens=final_spec.output_tokens,
        is_archived=False,
    )
    session.add(event)
    await session.flush()
    await ContextBatchService(session).assign_event_to_batch(event)
    await session.commit()
    return event


async def _collect_task_metrics(
    session: AsyncSession,
    run_ids: dict[str, UUID],
    *,
    cached_tokens: int,
    tasks: tuple[DialogueTaskSpec, ...] = _DIALOGUE_TASKS,
) -> dict[str, DialogueTaskMetrics]:
    metrics: dict[str, DialogueTaskMetrics] = {}
    for task in tasks:
        events = (
            (
                await session.execute(
                    select(Event)
                    .where(Event.run_id == run_ids[task.name])
                    .order_by(Event.sequence.asc())
                )
            )
            .scalars()
            .all()
        )
        metrics[task.name] = DialogueTaskMetrics(
            consumed_tokens=sum(
                int(event.input_tokens or 0) + int(event.output_tokens or 0)
                for event in events
            ),
            cached_tokens=cached_tokens if task.name == "calculator_followup" else 0,
            completion_event_count=len(events),
        )
    return metrics


async def test_context_batch_dialogue_metrics_track_tokens_cache_and_events(
    event_gc_sessionmaker: async_sessionmaker[AsyncSession],
):
    async with event_gc_sessionmaker() as session:
        user_id, workspace_id, run_ids, _events = await _seed_dialogue_tasks(session)
        service = ContextBatchService(session)
        current_run_id = run_ids["calculator_followup"]
        executor = DefaultExecutor(
            {
                "workspace_id": str(workspace_id),
                "run_id": str(current_run_id),
            }
        )

        before_plan = await service.build_load_plan(
            workspace_id,
            run_id=current_run_id,
        )
        before_messages, _ = executor.get_messages_and_tools_from_batch_plan(
            key_contents=before_plan.key_contents,
            load_all_events=before_plan.load_all_events,
        )
        before_prompt = _render_prompt(before_messages)

        await _append_final_calculator_answer(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            run_id=current_run_id,
        )
        after_plan = await service.build_load_plan(
            workspace_id,
            run_id=current_run_id,
        )
        after_messages, _ = executor.get_messages_and_tools_from_batch_plan(
            key_contents=after_plan.key_contents,
            load_all_events=after_plan.load_all_events,
        )
        after_prompt = _render_prompt(after_messages)

        assert after_prompt.startswith(before_prompt)
        cached_tokens = _estimated_token_count(
            _common_prefix(before_prompt, after_prompt)
        )
        metrics = await _collect_task_metrics(
            session,
            run_ids,
            cached_tokens=cached_tokens,
        )

        assert metrics["baseline_summary"] == DialogueTaskMetrics(
            consumed_tokens=128,
            cached_tokens=0,
            completion_event_count=2,
        )
        assert metrics["context_lookup"] == DialogueTaskMetrics(
            consumed_tokens=379,
            cached_tokens=0,
            completion_event_count=6,
        )
        assert metrics["calculator_followup"] == DialogueTaskMetrics(
            consumed_tokens=370,
            cached_tokens=_estimated_token_count(before_prompt),
            completion_event_count=5,
        )
        assert len(after_plan.load_all_events) == len(before_plan.load_all_events) + 1
        assert any("stability budget" in content for content in after_plan.key_contents)
        assert "stream-noise-that-should-not-enter-prompts" not in after_prompt


async def test_open_source_agent_benchmarks_map_to_distilled_integration_cases(
    event_gc_sessionmaker: async_sessionmaker[AsyncSession],
):
    local_scenarios = {
        scenario
        for benchmark in _OPEN_SOURCE_AGENT_BENCHMARKS
        for scenario in benchmark.local_scenarios
    }
    heavy_benchmarks = {
        benchmark.name
        for benchmark in _OPEN_SOURCE_AGENT_BENCHMARKS
        if not benchmark.local_scenarios
    }

    assert local_scenarios == {
        "tau_retail_exchange",
        "bfcl_parallel_tools",
        "terminal_verifier",
    }
    assert heavy_benchmarks == {"SWE-bench", "WebArena/OSWorld"}

    async with event_gc_sessionmaker() as session:
        _user_id, workspace_id, run_ids, _events = await _seed_dialogue_task_specs(
            session,
            _AGENT_BENCHMARK_DIALOGUE_TASKS,
        )
        metrics = await _collect_task_metrics(
            session,
            run_ids,
            cached_tokens=0,
            tasks=_AGENT_BENCHMARK_DIALOGUE_TASKS,
        )

        assert metrics["tau_retail_exchange"] == DialogueTaskMetrics(
            consumed_tokens=773,
            cached_tokens=0,
            completion_event_count=9,
        )
        assert metrics["bfcl_parallel_tools"] == DialogueTaskMetrics(
            consumed_tokens=433,
            cached_tokens=0,
            completion_event_count=7,
        )
        assert metrics["terminal_verifier"] == DialogueTaskMetrics(
            consumed_tokens=725,
            cached_tokens=0,
            completion_event_count=8,
        )

        service = ContextBatchService(session)
        bfcl_run_id = run_ids["bfcl_parallel_tools"]
        bfcl_plan = await service.build_load_plan(workspace_id, run_id=bfcl_run_id)
        executor = DefaultExecutor(
            {
                "workspace_id": str(workspace_id),
                "run_id": str(bfcl_run_id),
            }
        )
        messages, _ = executor.get_messages_and_tools_from_batch_plan(
            key_contents=bfcl_plan.key_contents,
            load_all_events=bfcl_plan.load_all_events,
        )
        parallel_call = next(
            message
            for message in messages
            if message.role == "assistant"
            and message.tool_calls
            and {call.id for call in message.tool_calls}
            == {"call-customer", "call-invoice"}
        )
        tool_result_ids = [
            message.tool_call_id
            for message in messages
            if message.role == "tool"
            and message.tool_call_id in {"call-customer", "call-invoice"}
        ]
        assert [call.name for call in parallel_call.tool_calls or []] == [
            "get_customer",
            "get_invoice",
        ]
        assert tool_result_ids == ["call-customer", "call-invoice"]

        final_states = (
            (
                await session.execute(
                    select(Event.payload)
                    .where(
                        Event.run_id.in_(
                            [
                                run_ids["tau_retail_exchange"],
                                run_ids["terminal_verifier"],
                            ]
                        ),
                        Event.event_type == EventType.AGENT_MESSAGE.value,
                    )
                    .order_by(Event.sequence.asc())
                )
            )
            .scalars()
            .all()
        )
        assert {"exchange_state": "created"} in [
            payload.get("final_state") for payload in final_states
        ]
        assert {"verifier_exit_code": 0} in [
            payload.get("final_state") for payload in final_states
        ]


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
