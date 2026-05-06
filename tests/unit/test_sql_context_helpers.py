"""Unit tests for SQL-backed context retrieval helpers."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from structure.core.enums import ContextType, EventType
from structure.core.enums.context import ContextScope
from structure.models.context.context import Context
from structure.plugins.executors.default.concrete import (
    _parse_context_tool_schema,
    _tool_name_from_context_path,
)
from structure.plugins.tools.context import _sql_context as sql_context
from structure.services.events.event_gc import EventGarbageCollector
from structure.utils.context import build_context_path, build_path


def _context(
    path: str,
    content: str,
    *,
    glance: str | None = None,
    tags: list[str] | None = None,
    rating_sum: float = 0.0,
    rating_count: int = 0,
) -> Context:
    return Context(
        user_id=uuid4(),
        source_id=uuid4(),
        scope=ContextScope.WORKSPACE,
        context_type=ContextType.WORKSPACE,
        path=path,
        glance=glance or content[:32],
        content=content,
        tags=tags or [],
        meta={"origin": "unit-test"},
        rating_sum=rating_sum,
        rating_count=rating_count,
    )


async def _install_contexts(monkeypatch: pytest.MonkeyPatch, contexts: list[Context]):
    service = SimpleNamespace(contexts=contexts)

    async def load_service(*_args, **_kwargs):
        return service

    monkeypatch.setattr(sql_context, "load_service", load_service)


@pytest.mark.unit
def test_normalize_path_accepts_legacy_and_canonical_forms():
    assert sql_context.normalize_path("tools/read_context") == "/tools/read-context"
    assert sql_context.normalize_path("/tools/read_context") == "/tools/read-context"
    assert sql_context.normalize_path(" /tools/read_context/ ") == "/tools/read-context"
    assert sql_context.normalize_path("/tool/read_context") == "/tools/read-context"


@pytest.mark.unit
def test_build_context_path_uses_semantic_segments():
    assert build_context_path("tool", "Read Context") == "/tools/read-context"
    assert (
        build_context_path("tools", "read_context", "readme.md")
        == "/tools/read-context/readme.md"
    )
    assert (
        build_context_path("tool", "read_context", "schema.md")
        == "/tools/read-context/schema.md"
    )
    assert (
        build_path("knowledge", "Python Guide", "documents", "README.md")
        == "/knowledge/python-guide/documents/readme.md"
    )


@pytest.mark.unit
def test_tool_schema_path_derives_executable_name_from_parent_directory():
    assert _tool_name_from_context_path("/tools/read-context/schema.md") == "read_context"
    assert _tool_name_from_context_path("/tools/read-context/readme.md") is None


@pytest.mark.unit
def test_tool_schema_parser_accepts_json_fenced_markdown():
    schema = _parse_context_tool_schema(
        '# Tool Schema\n\n```json\n{"type":"function","function":{"name":"read_context"}}\n```'
    )

    assert schema["function"]["name"] == "read_context"


@pytest.mark.unit
def test_disclosure_levels_hide_and_reveal_expected_fields():
    ctx = _context(
        "/tools/read_context",
        "Full tool schema and implementation notes",
        glance="Read context",
        tags=["tool"],
        rating_sum=1.5,
        rating_count=2,
    )

    glance = ctx.disclose("glance")
    overview = ctx.disclose("overview")
    detail = ctx.disclose("detail")

    assert glance == {"path": "/tools/read_context", "glance": "Read context"}
    assert "content" not in overview
    assert overview["rating"] == {"avg": 0.75, "count": 2}
    assert detail["content"] == "Full tool schema and implementation notes"
    assert detail["context_type"] == ContextType.WORKSPACE


@pytest.mark.unit
async def test_list_contexts_respects_prefix_depth_and_min_rating(monkeypatch):
    contexts = [
        _context("/tools/read_context", "schema", rating_sum=1, rating_count=1),
        _context("/tools/nested/deep", "deep schema", rating_sum=1, rating_count=1),
        _context("/knowledge/guide", "guide", rating_sum=1, rating_count=1),
        _context("/tools/low", "low quality", rating_sum=-1, rating_count=1),
    ]
    await _install_contexts(monkeypatch, contexts)

    results = await sql_context.list_contexts(
        None,
        uuid4(),
        prefix="/tools",
        recursive=False,
        min_rating=0.0,
    )

    assert [item["path"] for item in results] == ["/tools/read_context"]


@pytest.mark.unit
async def test_glob_contexts_filters_tags_and_rating(monkeypatch):
    contexts = [
        _context("/tools/read", "schema", tags=["tool"], rating_sum=1, rating_count=1),
        _context("/tools/write", "schema", tags=["tool"], rating_sum=-1, rating_count=1),
        _context("/tools/other", "schema", tags=["other"], rating_sum=1, rating_count=1),
    ]
    await _install_contexts(monkeypatch, contexts)

    results = await sql_context.glob_contexts(
        None,
        uuid4(),
        pattern="/tools/*",
        tags=["tool"],
        min_rating=0.0,
    )

    assert [item["path"] for item in results] == ["/tools/read"]


@pytest.mark.unit
async def test_query_contexts_blends_text_score_and_rating(monkeypatch):
    contexts = [
        _context(
            "/knowledge/low-rated-python",
            "python schema reference",
            rating_sum=-1,
            rating_count=1,
        ),
        _context(
            "/knowledge/high-rated-python",
            "python schema guide",
            rating_sum=1,
            rating_count=1,
        ),
    ]
    await _install_contexts(monkeypatch, contexts)

    rating_first = await sql_context.query_contexts(
        None,
        uuid4(),
        query="python schema",
        prefix="/knowledge",
        alpha=0.0,
    )
    filtered = await sql_context.query_contexts(
        None,
        uuid4(),
        query="python schema",
        prefix="/knowledge",
        min_rating=0.0,
    )

    assert rating_first[0]["path"] == "/knowledge/high-rated-python"
    assert [item["path"] for item in filtered] == ["/knowledge/high-rated-python"]


@pytest.mark.unit
async def test_search_contexts_uses_regex_over_path_glance_and_content(monkeypatch):
    contexts = [
        _context("/knowledge/python", "async SQLAlchemy session patterns"),
        _context("/knowledge/java", "JVM notes"),
    ]
    await _install_contexts(monkeypatch, contexts)

    results = await sql_context.search_contexts(
        None,
        uuid4(),
        pattern="sqlalchemy",
        level="detail",
    )

    assert [item["path"] for item in results] == ["/knowledge/python"]
    assert results[0]["content"] == "async SQLAlchemy session patterns"


@pytest.mark.unit
def test_event_gc_keeps_pinned_events_and_removes_expired_transients():
    now = datetime.now(UTC)
    events = [
        SimpleNamespace(
            event_type=EventType.USER_MESSAGE,
            sequence=1,
            created_at=now,
            step=None,
        ),
        SimpleNamespace(
            event_type=EventType.AGENT_TOKEN,
            sequence=4,
            created_at=now,
            step=None,
        ),
        SimpleNamespace(
            event_type=EventType.TOOL_RESULT,
            sequence=1,
            created_at=now,
            step=None,
        ),
        SimpleNamespace(
            event_type=EventType.CONTEXT_CREATED,
            sequence=2,
            created_at=now,
            step=None,
        ),
    ]

    live = EventGarbageCollector().collect(events, current_step=5, now=now)

    assert [event.event_type for event in live] == [
        EventType.USER_MESSAGE,
        EventType.CONTEXT_CREATED,
    ]
