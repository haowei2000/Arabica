from types import SimpleNamespace

import pytest

from structure.plugins.executors.default.concrete import DefaultExecutor


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def execute(self, statement):
        self.statement = statement
        return _FakeResult(self.rows)


@pytest.mark.asyncio
async def test_global_history_orders_by_created_at_then_reverses(monkeypatch):
    newest = SimpleNamespace(name="newest")
    oldest = SimpleNamespace(name="oldest")
    fake_session = _FakeSession([newest, oldest])

    def fake_get_session(name):
        assert name == "structure"
        return fake_session

    monkeypatch.setattr("structure.extensions.database.get_session", fake_get_session)

    executor = DefaultExecutor(
        {
            "workspace_id": "00000000-0000-0000-0000-000000000001",
            "run_id": "00000000-0000-0000-0000-000000000002",
            "api_key": "test-key",
            "base_url": "http://example.test/v1",
        }
    )

    rows = await executor._fetch_events(global_scope=True, limit=2)

    assert rows == [oldest, newest]

    sql = str(fake_session.statement)
    assert "ORDER BY event.created_at DESC, event.sequence DESC" in sql
