"""Unit tests for /api/tools/* endpoints."""

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from structure.app import app
from structure.core.dependencies.auth import get_current_user
from structure.extensions.database import get_structure_db
from tests.unit.routers.conftest import make_user, USER_ID, TOOL_ID

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


# ── POST /api/tools/ ─────────────────────────────────────────────


class TestCreateTool:
    def test_create_success(self, client):
        tool = make_tool()

        with (
            patch("structure.routers.context.tools.tools.ToolCRUD") as MockCRUD,
            patch("structure.celery_worker.tasks.context_sync_tasks.sync_tool_to_contexts") as mock_task,
        ):
            crud = MockCRUD.return_value
            crud.create_tool = AsyncMock(return_value=tool)
            mock_task.delay = MagicMock()

            resp = client.post(f"{BASE}/", json=CREATE_PAYLOAD)

        assert resp.status_code == 201
        assert resp.json()["name"] == "my_api"

    def test_create_duplicate_name_raises_value_error(self, client):
        with patch("structure.routers.context.tools.tools.ToolCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.create_tool = AsyncMock(side_effect=ValueError("Tool already exists"))

            resp = client.post(f"{BASE}/", json=CREATE_PAYLOAD)

        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"]

    def test_create_missing_required(self, client):
        resp = client.post(f"{BASE}/", json={"name": "only_name"})
        assert resp.status_code == 422


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


# ── PATCH /api/tools/{id} ─────────────────────────────────────────


class TestUpdateTool:
    def test_update_success(self, client):
        tool = make_tool()

        with (
            patch("structure.routers.context.tools.tools.ToolCRUD") as MockCRUD,
            patch("structure.celery_worker.tasks.context_sync_tasks.sync_tool_to_contexts") as mock_task,
        ):
            crud = MockCRUD.return_value
            crud.get_tool_by_id = AsyncMock(return_value=tool)  # first call (type check)
            crud.update_tool = AsyncMock(return_value=tool)
            mock_task.delay = MagicMock()

            resp = client.patch(
                f"{BASE}/{TOOL_ID}",
                json={"description": "Updated description"},
            )

        assert resp.status_code == 200

    def test_update_not_found(self, client):
        with patch("structure.routers.context.tools.tools.ToolCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.get_tool_by_id = AsyncMock(return_value=None)
            crud.update_tool = AsyncMock(return_value=None)

            resp = client.patch(
                f"{BASE}/{uuid4()}",
                json={"description": "x"},
            )

        assert resp.status_code == 404

    def test_update_inner_tool_forbidden(self, client):
        inner = make_tool(tool_type="inner")

        with patch("structure.routers.context.tools.tools.ToolCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.get_tool_by_id = AsyncMock(return_value=inner)

            resp = client.patch(
                f"{BASE}/{TOOL_ID}",
                json={"description": "x"},
            )

        assert resp.status_code == 403


# ── DELETE /api/tools/{id} ────────────────────────────────────────


class TestDeleteTool:
    def test_delete_success(self, client):
        tool = make_tool()

        with patch("structure.routers.context.tools.tools.ToolCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.get_tool_by_id = AsyncMock(return_value=tool)
            crud.delete_tool = AsyncMock(return_value=True)

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


# ── GET /api/tools/templates ──────────────────────────────────────


class TestTemplates:
    def test_list_templates(self, client):
        with patch("structure.routers.context.tools.tools.get_all_templates") as mock_get:
            mock_get.return_value = []
            resp = client.get(f"{BASE}/templates")

        assert resp.status_code == 200

    def test_get_template_not_found(self, client):
        with (
            patch.dict("structure.routers.context.tools.tools.TOOL_TEMPLATES", {}, clear=True),
            patch("structure.routers.context.tools.tools.get_inner_tool_templates", return_value={}),
        ):
            resp = client.get(f"{BASE}/templates/nonexistent")

        assert resp.status_code == 404


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
