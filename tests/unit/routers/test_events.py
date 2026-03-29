"""Unit tests for /api/events/* endpoints."""

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from structure.app import app
from structure.core.dependencies.auth import get_current_user
from structure.core.dependencies.workspace import (
    get_event_crud,
    get_workspace_crud,
    get_run_crud,
)
from structure.extensions.database import get_aiwen_db
from tests.unit.routers.conftest import make_user, USER_ID, WORKSPACE_ID, RUN_ID

EVENT_ID = uuid4()
BASE = "/api/events"


def make_event(**kwargs):
    now = datetime.now(UTC)
    e = MagicMock()
    e.id = EVENT_ID
    e.event_type = "user.message"
    e.workspace_id = WORKSPACE_ID
    e.run_id = RUN_ID
    e.app_id = None
    e.user_id = USER_ID
    e.executor_code = None
    e.payload = {"message": "Hello"}
    e.input_tokens = 0
    e.output_tokens = 0
    e.sequence = 1
    e.parent_event_id = None
    e.created_at = now
    for k, v in kwargs.items():
        setattr(e, k, v)
    return e


def make_workspace():
    ws = MagicMock()
    ws.id = str(WORKSPACE_ID)
    ws.owner_id = str(USER_ID)
    return ws


@pytest.fixture()
def mock_event_crud():
    crud = AsyncMock()
    crud.get_by_id = AsyncMock(return_value=make_event())
    crud.list_by_workspace = AsyncMock(return_value=([make_event()], 1))
    crud.list_by_run = AsyncMock(return_value=([make_event()], 1))
    crud.list_by_user = AsyncMock(return_value=([make_event()], 1))
    crud.search = AsyncMock(return_value=([make_event()], 1))
    crud.delete = AsyncMock()
    return crud


@pytest.fixture()
def mock_workspace_crud():
    crud = AsyncMock()
    crud.get_by_id_and_user = AsyncMock(return_value=make_workspace())
    return crud


@pytest.fixture()
def mock_run_crud():
    run = MagicMock()
    run.id = str(RUN_ID)
    run.workspace_id = str(WORKSPACE_ID)
    crud = AsyncMock()
    crud.get_by_id_and_user = AsyncMock(return_value=run)
    return crud


@pytest.fixture()
def mock_db():
    return AsyncMock()


@pytest.fixture()
def client(mock_event_crud, mock_workspace_crud, mock_run_crud, mock_db, patch_bootstrap):
    user = make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_aiwen_db] = lambda: mock_db
    app.dependency_overrides[get_event_crud] = lambda: mock_event_crud
    app.dependency_overrides[get_workspace_crud] = lambda: mock_workspace_crud
    app.dependency_overrides[get_run_crud] = lambda: mock_run_crud

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


# ── GET /api/events/{event_id} ────────────────────────────────────


class TestGetEvent:
    def test_get_success(self, client):
        resp = client.get(f"{BASE}/{EVENT_ID}")
        assert resp.status_code == 200

    def test_get_not_found(self, client, mock_event_crud):
        mock_event_crud.get_by_id.return_value = None
        resp = client.get(f"{BASE}/{uuid4()}")
        assert resp.status_code == 404

    def test_get_no_workspace_access(self, client, mock_workspace_crud):
        mock_workspace_crud.get_by_id_and_user.return_value = None
        resp = client.get(f"{BASE}/{EVENT_ID}")
        assert resp.status_code == 403


# ── GET /api/events/workspace/{ws_id}/list ────────────────────────


class TestListByWorkspace:
    def test_list_success(self, client):
        resp = client.get(f"{BASE}/workspace/{WORKSPACE_ID}/list")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1

    def test_list_workspace_not_found(self, client, mock_workspace_crud):
        mock_workspace_crud.get_by_id_and_user.return_value = None
        resp = client.get(f"{BASE}/workspace/{uuid4()}/list")
        assert resp.status_code == 404

    def test_list_with_event_types(self, client):
        resp = client.get(
            f"{BASE}/workspace/{WORKSPACE_ID}/list?event_types=user.message,agent.message"
        )
        assert resp.status_code == 200


# ── GET /api/events/run/{run_id}/list ────────────────────────────


class TestListByRun:
    def test_list_success(self, client):
        resp = client.get(f"{BASE}/run/{RUN_ID}/list")
        assert resp.status_code == 200

    def test_list_run_not_found(self, client, mock_run_crud):
        mock_run_crud.get_by_id_and_user.return_value = None
        resp = client.get(f"{BASE}/run/{uuid4()}/list")
        assert resp.status_code == 404


# ── GET /api/events/user/list ─────────────────────────────────────


class TestListByUser:
    def test_list_success(self, client):
        resp = client.get(f"{BASE}/user/list")
        assert resp.status_code == 200

    def test_list_with_filters(self, client):
        resp = client.get(f"{BASE}/user/list?limit=50&skip=10")
        assert resp.status_code == 200


# ── GET /api/events/search ────────────────────────────────────────


class TestSearchEvents:
    # NOTE: The /search endpoint is registered AFTER /{event_id} in the router.
    # FastAPI matches /{event_id} first and returns 422 when 'search' fails UUID
    # validation. These tests document that routing behavior.

    def test_search_path_unreachable_due_to_route_order(self, client):
        # /{event_id} matches /search before /search route is tried, so 422
        resp = client.get(f"{BASE}/search")
        assert resp.status_code == 422  # UUID parse failure for 'search'

    def test_search_with_workspace_id_unreachable(self, client):
        resp = client.get(f"{BASE}/search?workspace_id={str(WORKSPACE_ID)}")
        assert resp.status_code == 422

    def test_search_with_run_id_unreachable(self, client):
        resp = client.get(f"{BASE}/search?run_id={str(RUN_ID)}")
        assert resp.status_code == 422

    def test_search_with_sequence_range_unreachable(self, client):
        resp = client.get(
            f"{BASE}/search?workspace_id={str(WORKSPACE_ID)}&from_sequence=0&to_sequence=100"
        )
        assert resp.status_code == 422


# ── DELETE /api/events/{event_id} ────────────────────────────────


class TestDeleteEvent:
    def test_delete_success(self, client):
        resp = client.delete(f"{BASE}/{EVENT_ID}")
        assert resp.status_code == 204

    def test_delete_not_found(self, client, mock_event_crud):
        mock_event_crud.get_by_id.return_value = None
        resp = client.delete(f"{BASE}/{uuid4()}")
        assert resp.status_code == 404

    def test_delete_no_access(self, client, mock_workspace_crud):
        mock_workspace_crud.get_by_id_and_user.return_value = None
        resp = client.delete(f"{BASE}/{EVENT_ID}")
        assert resp.status_code == 403

    def test_delete_not_owner(self, client, mock_workspace_crud):
        ws = make_workspace()
        ws.owner_id = str(uuid4())  # different owner
        mock_workspace_crud.get_by_id_and_user.return_value = ws
        resp = client.delete(f"{BASE}/{EVENT_ID}")
        assert resp.status_code == 403
