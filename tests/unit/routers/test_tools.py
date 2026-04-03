"""Unit tests for /api/tools/* endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from structure.app import app
from structure.core.dependencies.auth import get_current_user
from structure.extensions.database import get_structure_db
from tests.unit.routers.conftest import TOOL_ID, USER_ID, make_user

BASE = "/api/tools"


def make_tool(**kwargs):
    now = datetime.now(UTC)
    t = MagicMock()
    t.id = TOOL_ID
    t.tool_code = "ext_myapi_abcd1234"
    t.user_id = USER_ID
    t.workspace_id = None
    t.name = "my_api"
    t.display_name = "My API Tool"
    t.description = "A test HTTP tool"
    t.tool_type = "external"
    t.inner_tool_name = "http_request"
    t.parameter_mapping = None
    t.chain = None
    t.input_schema = {"type": "object", "properties": {}}
    t.output_schema = None
    t.category = "custom"
    t.tags = ["api"]
    t.version = 1
    t.timeout = 30
    t.enabled = True
    t.is_public = False
    t.verified = False
    t.usage_count = 0
    t.last_used_at = None
    t.created_at = now
    t.updated_at = now
    for k, v in kwargs.items():
        setattr(t, k, v)
    return t


CREATE_PAYLOAD = {
    "name": "my_api",
    "display_name": "My API Tool",
    "description": "A test HTTP tool",
    "input_schema": {"type": "object"},
    "inner_tool_name": "http_request",
}


@pytest.fixture()
def mock_db():
    return AsyncMock()


@pytest.fixture()
def client(mock_db, patch_bootstrap):
    user = make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_structure_db] = lambda: mock_db

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.mark.skip(reason="Create tool endpoint removed from router")
class TestCreateTool:
    def test_create_success(self, client):
        pass

    def test_create_duplicate_name_raises_value_error(self, client):
        pass

    def test_create_missing_required(self, client):
        pass


# ── GET /api/tools/ ────────────────────────────────────────────────


class TestListTools:
    def test_list_success(self, client):
        tool = make_tool()

        with patch("structure.routers.context.tools.tools.ToolCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.list_user_tools = AsyncMock(return_value=[tool])

            resp = client.get(f"{BASE}/")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1

    def test_list_with_filters(self, client):
        with patch("structure.routers.context.tools.tools.ToolCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.list_user_tools = AsyncMock(return_value=[])

            resp = client.get(f"{BASE}/?tool_type=external")

        assert resp.status_code == 200


# ── GET /api/tools/{id} ───────────────────────────────────────────


class TestGetTool:
    def test_get_success(self, client):
        tool = make_tool()

        with patch("structure.routers.context.tools.tools.ToolCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.get_tool_by_id = AsyncMock(return_value=tool)

            resp = client.get(f"{BASE}/{TOOL_ID}")

        assert resp.status_code == 200

    def test_get_not_found(self, client):
        with patch("structure.routers.context.tools.tools.ToolCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.get_tool_by_id = AsyncMock(return_value=None)

            resp = client.get(f"{BASE}/{uuid4()}")

        assert resp.status_code == 404


@pytest.mark.skip(reason="Update tool endpoint (PATCH) removed from router")
class TestUpdateTool:
    def test_update_success(self, client):
        pass

    def test_update_not_found(self, client):
        pass

    def test_update_inner_tool_forbidden(self, client):
        pass


# ── DELETE /api/tools/{id} ────────────────────────────────────────


class TestDeleteTool:
    def test_delete_success(self, client):
        tool = make_tool()

        with (
            patch("structure.routers.context.tools.tools.ToolCRUD") as MockCRUD,
            patch("structure.celery_worker.tasks.context_sync_tasks.delete_resource_contexts") as mock_task,
        ):
            crud = MockCRUD.return_value
            crud.get_tool_by_id = AsyncMock(return_value=tool)
            crud.delete_tool = AsyncMock(return_value=True)
            mock_task.delay = MagicMock()

            resp = client.delete(f"{BASE}/{TOOL_ID}")

        assert resp.status_code == 204

    def test_delete_not_found(self, client):
        with patch("structure.routers.context.tools.tools.ToolCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.get_tool_by_id = AsyncMock(return_value=None)
            crud.delete_tool = AsyncMock(return_value=False)

            resp = client.delete(f"{BASE}/{uuid4()}")

        assert resp.status_code == 404

    def test_delete_inner_tool_forbidden(self, client):
        inner = make_tool(tool_type="inner")

        with patch("structure.routers.context.tools.tools.ToolCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.get_tool_by_id = AsyncMock(return_value=inner)

            resp = client.delete(f"{BASE}/{TOOL_ID}")

        assert resp.status_code == 403


@pytest.mark.skip(reason="Templates endpoint removed from router")
class TestTemplates:
    def test_list_templates(self, client):
        pass

    def test_get_template_not_found(self, client):
        pass


# ── POST /api/tools/{id}/test ─────────────────────────────────────


class TestToolTest:
    def test_test_tool_not_found(self, client):
        with patch("structure.routers.context.tools.tools.ToolCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.get_tool_by_id = AsyncMock(return_value=None)

            resp = client.post(
                f"{BASE}/{uuid4()}/test",
                json={"parameters": {}},
            )

        assert resp.status_code == 404
