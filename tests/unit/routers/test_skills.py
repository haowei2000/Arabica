"""Unit tests for /api/agent/skills/* endpoints."""

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from structure.app import app
from structure.core.dependencies.auth import get_current_user
from structure.extensions.database import get_aiwen_db
from tests.unit.routers.conftest import make_user, USER_ID, SKILL_ID


def make_skill(**kwargs):
    now = datetime.now(UTC)
    s = MagicMock()
    s.id = SKILL_ID
    s.user_id = USER_ID
    s.source_id = None
    s.workspace_id = None
    s.path = None
    s.name = "Test Skill"
    s.description = "A test skill"
    s.content = "# Test\nThis is a test skill."
    s.glance = "test skill"
    s.summary = None
    s.tags = ["test"]
    s.files = None
    s.created_at = now
    s.updated_at = now
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


@pytest.fixture()
def mock_db():
    return AsyncMock()


@pytest.fixture()
def client(mock_db, patch_bootstrap):
    user = make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_aiwen_db] = lambda: mock_db

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


BASE = "/api/agent/skills"


class TestCreateSkill:
    def test_create_success(self, client):
        skill = make_skill()

        with (
            patch("structure.routers.context.skills.SkillCRUD") as MockCRUD,
            patch("structure.routers.context.skills.SkillProcessor") as MockProc,
            patch("structure.celery_worker.tasks.context_sync_tasks.sync_skill_to_contexts") as mock_task,
        ):
            crud = MockCRUD.return_value
            crud.get_by_name = AsyncMock(return_value=None)
            crud.create = AsyncMock(return_value=skill)

            proc = MockProc.return_value
            proc.process_skill = AsyncMock(return_value=skill)

            mock_task.delay = MagicMock()

            resp = client.post(
                BASE,
                json={"name": "Test Skill", "content": "# Hello"},
            )

        assert resp.status_code == 201
        assert resp.json()["name"] == "Test Skill"
        mock_task.delay.assert_called_once()

    def test_create_duplicate_name(self, client):
        existing = make_skill()

        with patch("structure.routers.context.skills.SkillCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.get_by_name = AsyncMock(return_value=existing)

            resp = client.post(
                BASE,
                json={"name": "Test Skill", "content": "# Hello"},
            )

        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"]

    def test_create_missing_fields(self, client):
        resp = client.post(BASE, json={"name": "Only Name"})
        assert resp.status_code == 422

    def test_create_process_failure(self, client):
        skill = make_skill()

        with (
            patch("structure.routers.context.skills.SkillCRUD") as MockCRUD,
            patch("structure.routers.context.skills.SkillProcessor") as MockProc,
        ):
            crud = MockCRUD.return_value
            crud.get_by_name = AsyncMock(return_value=None)
            crud.create = AsyncMock(return_value=skill)

            proc = MockProc.return_value
            proc.process_skill = AsyncMock(side_effect=Exception("LLM error"))

            resp = client.post(
                BASE,
                json={"name": "New Skill", "content": "# Hello"},
            )

        assert resp.status_code == 500


class TestGetSkill:
    def test_get_success(self, client):
        skill = make_skill()

        with patch("structure.routers.context.skills.SkillCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.get_by_id = AsyncMock(return_value=skill)

            resp = client.get(f"{BASE}/{SKILL_ID}")

        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Skill"

    def test_get_not_found(self, client):
        with patch("structure.routers.context.skills.SkillCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.get_by_id = AsyncMock(return_value=None)

            resp = client.get(f"{BASE}/{uuid4()}")

        assert resp.status_code == 404


class TestUpdateSkill:
    def test_update_success(self, client):
        skill = make_skill()

        with (
            patch("structure.routers.context.skills.SkillCRUD") as MockCRUD,
            patch("structure.routers.context.skills.SkillProcessor") as MockProc,
            patch("structure.celery_worker.tasks.context_sync_tasks.sync_skill_to_contexts") as mock_task,
        ):
            crud = MockCRUD.return_value
            crud.update = AsyncMock(return_value=skill)

            proc = MockProc.return_value
            proc.process_skill = AsyncMock(return_value=skill)

            mock_task.delay = MagicMock()

            resp = client.put(
                f"{BASE}/{SKILL_ID}",
                json={"name": "Updated", "content": "# Updated"},
            )

        assert resp.status_code == 200
        mock_task.delay.assert_called_once()

    def test_update_not_found(self, client):
        with (
            patch("structure.routers.context.skills.SkillCRUD") as MockCRUD,
            patch("structure.routers.context.skills.SkillProcessor"),
        ):
            crud = MockCRUD.return_value
            crud.update = AsyncMock(return_value=None)

            resp = client.put(
                f"{BASE}/{uuid4()}",
                json={"name": "X"},
            )

        assert resp.status_code == 404


class TestDeleteSkill:
    def test_delete_success(self, client):
        with patch("structure.routers.context.skills.SkillCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.delete = AsyncMock(return_value=True)

            resp = client.delete(f"{BASE}/{SKILL_ID}")

        assert resp.status_code == 204

    def test_delete_not_found(self, client):
        with patch("structure.routers.context.skills.SkillCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.delete = AsyncMock(return_value=False)

            resp = client.delete(f"{BASE}/{uuid4()}")

        assert resp.status_code == 404


class TestListSkills:
    def test_list_success(self, client):
        skill = make_skill()

        with patch("structure.routers.context.skills.SkillCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.list = AsyncMock(return_value=([skill], 1))

            resp = client.get(BASE)

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1

    def test_list_with_tags(self, client):
        with patch("structure.routers.context.skills.SkillCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.list = AsyncMock(return_value=([], 0))

            resp = client.get(f"{BASE}?tags=python,coding")

        assert resp.status_code == 200


class TestSearchSkills:
    def test_search_success(self, client):
        skill = make_skill()

        with patch("structure.routers.context.skills.SkillCRUD") as MockCRUD:
            crud = MockCRUD.return_value
            crud.search = AsyncMock(return_value=([skill], 1))

            resp = client.get(f"{BASE}/search/query?q=test")

        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_search_missing_query(self, client):
        resp = client.get(f"{BASE}/search/query")
        assert resp.status_code == 422


class TestProcessSkill:
    def test_process_success(self, client):
        skill = make_skill()

        with (
            patch("structure.routers.context.skills.SkillCRUD") as MockCRUD,
            patch("structure.routers.context.skills.SkillProcessor") as MockProc,
            patch("structure.celery_worker.tasks.context_sync_tasks.sync_skill_to_contexts") as mock_task,
        ):
            crud = MockCRUD.return_value
            crud.get_by_id = AsyncMock(return_value=skill)

            proc = MockProc.return_value
            proc.process_skill = AsyncMock(return_value=skill)

            mock_task.delay = MagicMock()

            resp = client.post(f"{BASE}/{SKILL_ID}/process")

        assert resp.status_code == 200
        mock_task.delay.assert_called_once()

    def test_process_not_found(self, client):
        with (
            patch("structure.routers.context.skills.SkillCRUD") as MockCRUD,
            patch("structure.routers.context.skills.SkillProcessor"),
        ):
            crud = MockCRUD.return_value
            crud.get_by_id = AsyncMock(return_value=None)

            resp = client.post(f"{BASE}/{uuid4()}/process")

        assert resp.status_code == 404
