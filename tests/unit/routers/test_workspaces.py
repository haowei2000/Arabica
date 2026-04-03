"""Unit tests for /api/workspaces/* endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from structure.app import app
from structure.core.dependencies.auth import get_current_user
from structure.core.dependencies.workspace import (
    get_workspace_crud,
    get_workspace_member_crud,
)
from structure.extensions.database import get_structure_db
from tests.unit.routers.conftest import (
    USER_ID,
    WORKSPACE_ID,
    make_user,
)


def make_workspace(**kwargs):
    now = datetime.now(UTC)
    ws = MagicMock()
    ws.id = str(WORKSPACE_ID)
    ws.name = "Test Workspace"
    ws.description = None
    ws.owner_id = str(USER_ID)
    ws.app_id = None
    ws.executor_code = None
    ws.executor_config = None
    ws.visibility = "private"
    ws.is_shared = False
    ws.settings = None
    ws.status = "active"
    ws.run_count = 0
    ws.member_count = 1
    ws.legacy_conversation_id = None
    ws.created_at = now
    ws.updated_at = now
    for k, v in kwargs.items():
        setattr(ws, k, v)
    return ws


def make_member(**kwargs):
    now = datetime.now(UTC)
    m = MagicMock()
    m.id = str(uuid4())
    m.workspace_id = str(WORKSPACE_ID)
    m.user_id = str(USER_ID)
    m.role = "owner"
    m.invited_by = None
    m.invitation_status = "accepted"
    m.joined_at = now
    m.created_at = now
    m.updated_at = now
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


@pytest.fixture()
def mock_workspace_crud():
    crud = AsyncMock()
    crud.create = AsyncMock(return_value=make_workspace())
    crud.get_by_id_and_user = AsyncMock(return_value=make_workspace())
    crud.update = AsyncMock(return_value=make_workspace())
    crud.delete = AsyncMock(return_value=True)
    crud.list_by_user = AsyncMock(return_value=([make_workspace()], 1))
    return crud


@pytest.fixture()
def mock_member_crud():
    crud = AsyncMock()
    crud.get_user_role = AsyncMock(return_value="owner")
    crud.add_member = AsyncMock(return_value=make_member())
    crud.list_members = AsyncMock(return_value=([make_member()], 1))
    crud.update_role = AsyncMock(return_value=make_member())
    crud.remove_member = AsyncMock(return_value=True)
    return crud


@pytest.fixture()
def mock_db():
    db = AsyncMock()
    # simulate scalar results for context queries
    result = MagicMock()
    result.scalar_one = MagicMock(return_value=0)
    result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


@pytest.fixture()
def client(mock_workspace_crud, mock_member_crud, mock_db, patch_bootstrap):
    user = make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_structure_db] = lambda: mock_db
    app.dependency_overrides[get_workspace_crud] = lambda: mock_workspace_crud
    app.dependency_overrides[get_workspace_member_crud] = lambda: mock_member_crud

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


# ── POST /api/workspaces ──────────────────────────────────────────


class TestCreateWorkspace:
    def test_create_success(self, client):
        resp = client.post(
            "/api/workspaces",
            json={"name": "My Workspace"},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "Test Workspace"

    def test_create_with_invalid_app_id(self, client, mock_db):
        # DB returns no App row
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=result)

        resp = client.post(
            "/api/workspaces",
            json={"name": "My WS", "app_id": str(uuid4())},
        )
        assert resp.status_code == 400

    def test_create_missing_name(self, client):
        resp = client.post("/api/workspaces", json={})
        assert resp.status_code == 422


# ── GET /api/workspaces/{id} ──────────────────────────────────────


class TestGetWorkspace:
    def test_get_success(self, client):
        resp = client.get(f"/api/workspaces/{WORKSPACE_ID}")
        assert resp.status_code == 200
        assert resp.json()["id"] == str(WORKSPACE_ID)

    def test_get_not_found(self, client, mock_workspace_crud):
        mock_workspace_crud.get_by_id_and_user.return_value = None
        resp = client.get(f"/api/workspaces/{uuid4()}")
        assert resp.status_code == 404


# ── PATCH /api/workspaces/{id} ────────────────────────────────────


class TestUpdateWorkspace:
    def test_update_success(self, client):
        resp = client.patch(
            f"/api/workspaces/{WORKSPACE_ID}",
            json={"name": "Updated Name"},
        )
        assert resp.status_code == 200

    def test_update_not_found(self, client, mock_workspace_crud):
        mock_workspace_crud.update.return_value = None
        resp = client.patch(
            f"/api/workspaces/{WORKSPACE_ID}",
            json={"name": "X"},
        )
        assert resp.status_code == 404


# ── DELETE /api/workspaces/{id} ───────────────────────────────────


class TestDeleteWorkspace:
    def test_delete_success(self, client):
        resp = client.delete(f"/api/workspaces/{WORKSPACE_ID}")
        assert resp.status_code == 204

    def test_delete_not_found(self, client, mock_workspace_crud):
        mock_workspace_crud.delete.return_value = False
        resp = client.delete(f"/api/workspaces/{WORKSPACE_ID}")
        assert resp.status_code == 404


# ── GET /api/workspaces ───────────────────────────────────────────


class TestListWorkspaces:
    def test_list_success(self, client):
        resp = client.get("/api/workspaces")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1

    def test_list_with_pagination(self, client):
        resp = client.get("/api/workspaces?page=2&page_size=5")
        assert resp.status_code == 200


# ── Workspace members ─────────────────────────────────────────────


class TestWorkspaceMembers:
    def test_add_member_success(self, client):
        resp = client.post(
            f"/api/workspaces/{WORKSPACE_ID}/members",
            json={"user_id": str(uuid4()), "role": "viewer"},
        )
        assert resp.status_code == 201

    def test_add_member_forbidden(self, client, mock_member_crud):
        mock_member_crud.get_user_role.return_value = "viewer"
        resp = client.post(
            f"/api/workspaces/{WORKSPACE_ID}/members",
            json={"user_id": str(uuid4())},
        )
        assert resp.status_code == 403

    def test_add_member_workspace_not_found(self, client, mock_workspace_crud):
        mock_workspace_crud.get_by_id_and_user.return_value = None
        resp = client.post(
            f"/api/workspaces/{uuid4()}/members",
            json={"user_id": str(uuid4())},
        )
        assert resp.status_code == 404

    def test_add_member_already_member(self, client, mock_member_crud):
        mock_member_crud.add_member.return_value = None
        resp = client.post(
            f"/api/workspaces/{WORKSPACE_ID}/members",
            json={"user_id": str(uuid4())},
        )
        assert resp.status_code == 409

    def test_list_members_success(self, client):
        resp = client.get(f"/api/workspaces/{WORKSPACE_ID}/members")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_remove_member_success(self, client):
        resp = client.delete(
            f"/api/workspaces/{WORKSPACE_ID}/members/{USER_ID}"
        )
        assert resp.status_code == 204

    def test_remove_member_not_found(self, client, mock_member_crud):
        mock_member_crud.remove_member.return_value = False
        resp = client.delete(
            f"/api/workspaces/{WORKSPACE_ID}/members/{uuid4()}"
        )
        assert resp.status_code == 404


# ── Workspace contexts ────────────────────────────────────────────


class TestWorkspaceContexts:
    def test_list_contexts_success(self, client):
        resp = client.get(f"/api/workspaces/{WORKSPACE_ID}/contexts")
        assert resp.status_code == 200
        body = resp.json()
        assert "total" in body
        assert "items" in body

    def test_list_contexts_not_found(self, client, mock_workspace_crud):
        mock_workspace_crud.get_by_id_and_user.return_value = None
        resp = client.get(f"/api/workspaces/{uuid4()}/contexts")
        assert resp.status_code == 404
