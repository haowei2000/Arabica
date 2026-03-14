"""Unit tests for /api/agent/knowledge/* endpoints."""

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aiwen.app import app
from aiwen.core.dependencies.agents import get_knowledge_crud
from aiwen.core.dependencies.auth import get_current_user
from aiwen.extensions.database import get_aiwen_db
from tests.unit.routers.conftest import make_user, USER_ID, KNOWLEDGE_ID

BASE = "/api/agent/knowledge"


def make_knowledge(**kwargs):
    now = datetime.now(UTC)
    k = MagicMock()
    k.id = str(KNOWLEDGE_ID)
    k.name = "Test Knowledge"
    k.description = "A test knowledge base"
    k.user_id = str(USER_ID)
    k.owner_id = str(USER_ID)
    k.provider = "default"
    k.indexing_technique = "high_quality"
    k.embedding_model = None
    k.preprocess_id = None
    k.status = "active"
    k.permission = "private"
    k.meta = None
    k.document_count = 0
    k.chunk_count = 0
    k.created_at = now
    k.updated_at = now
    for kk, v in kwargs.items():
        setattr(k, kk, v)
    return k


@pytest.fixture()
def mock_knowledge_crud():
    crud = AsyncMock()
    crud.create = AsyncMock(return_value=make_knowledge())
    crud.get_by_id = AsyncMock(return_value=make_knowledge())
    crud.update = AsyncMock(return_value=make_knowledge())
    crud.delete = AsyncMock(return_value=True)
    crud.list_by_user = AsyncMock(return_value=([make_knowledge()], 1))
    crud.search = AsyncMock(return_value=([make_knowledge()], 1))
    return crud


@pytest.fixture()
def mock_db():
    return AsyncMock()


@pytest.fixture()
def client(mock_knowledge_crud, mock_db, patch_bootstrap):
    user = make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_aiwen_db] = lambda: mock_db
    app.dependency_overrides[get_knowledge_crud] = lambda: mock_knowledge_crud

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


# ── POST /api/agent/knowledge/create ─────────────────────────────


class TestCreateKnowledge:
    def test_create_success(self, client):
        with patch("aiwen.celery_worker.tasks.context_sync_tasks.sync_knowledge_to_contexts") as mock_task:
            mock_task.delay = MagicMock()

            resp = client.post(
                f"{BASE}/create",
                json={"name": "My KB"},
            )

        assert resp.status_code == 201
        assert resp.json()["name"] == "Test Knowledge"
        mock_task.delay.assert_called_once()

    def test_create_missing_name(self, client):
        resp = client.post(f"{BASE}/create", json={})
        assert resp.status_code == 422


# ── GET /api/agent/knowledge/{id}/get ────────────────────────────


class TestGetKnowledge:
    def test_get_success(self, client):
        resp = client.get(f"{BASE}/{KNOWLEDGE_ID}/get")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Knowledge"

    def test_get_not_found(self, client, mock_knowledge_crud):
        mock_knowledge_crud.get_by_id.return_value = None
        resp = client.get(f"{BASE}/{uuid4()}/get")
        assert resp.status_code == 404

    def test_get_forbidden_other_user(self, client, mock_knowledge_crud):
        kb = make_knowledge(user_id=str(uuid4()))  # different user
        mock_knowledge_crud.get_by_id.return_value = kb
        resp = client.get(f"{BASE}/{KNOWLEDGE_ID}/get")
        assert resp.status_code == 403


# ── POST /api/agent/knowledge/{id}/update ────────────────────────


class TestUpdateKnowledge:
    def test_update_success(self, client):
        resp = client.post(
            f"{BASE}/{KNOWLEDGE_ID}/update",
            json={"name": "Updated KB"},
        )
        assert resp.status_code == 200

    def test_update_not_found(self, client, mock_knowledge_crud):
        mock_knowledge_crud.get_by_id.return_value = None
        resp = client.post(
            f"{BASE}/{uuid4()}/update",
            json={"name": "x"},
        )
        assert resp.status_code == 404


# ── POST /api/agent/knowledge/{id}/delete ────────────────────────


class TestDeleteKnowledge:
    def test_delete_success(self, client):
        resp = client.post(f"{BASE}/{KNOWLEDGE_ID}/delete")
        assert resp.status_code == 204

    def test_delete_not_found(self, client, mock_knowledge_crud):
        mock_knowledge_crud.get_by_id.return_value = None
        resp = client.post(f"{BASE}/{uuid4()}/delete")
        assert resp.status_code == 404

    def test_delete_forbidden_other_user(self, client, mock_knowledge_crud):
        kb = make_knowledge(user_id=str(uuid4()))
        mock_knowledge_crud.get_by_id.return_value = kb
        resp = client.post(f"{BASE}/{KNOWLEDGE_ID}/delete")
        assert resp.status_code == 403


# ── GET /api/agent/knowledge/query ───────────────────────────────


class TestListKnowledge:
    def test_list_success(self, client, mock_knowledge_crud):
        mock_knowledge_crud.list = AsyncMock(return_value=([make_knowledge()], 1))
        resp = client.get(f"{BASE}/query")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1

    def test_list_with_pagination(self, client, mock_knowledge_crud):
        mock_knowledge_crud.list = AsyncMock(return_value=([], 0))
        resp = client.get(f"{BASE}/query?page=2&page_size=10")
        assert resp.status_code == 200


# ── GET /api/agent/knowledge/search ──────────────────────────────


class TestSearchKnowledge:
    def test_search_success(self, client):
        resp = client.get(f"{BASE}/search?q=test")
        assert resp.status_code == 200

    def test_search_missing_query(self, client):
        resp = client.get(f"{BASE}/search")
        assert resp.status_code == 422
