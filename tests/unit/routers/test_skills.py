"""Unit tests for /api/agent/skills/* endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from structure.app import app
from structure.core.dependencies.agents import get_skill_crud
from structure.core.dependencies.auth import get_current_user
from structure.extensions.database import get_structure_db
from structure.extensions.storage.global_storage import get_global_s3_storage
from tests.unit.routers.conftest import SKILL_ID, USER_ID, make_user


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
def mock_skill_crud():
    """Mock for endpoints that use Depends(get_skill_crud)."""
    crud = AsyncMock()
    crud.get_by_id = AsyncMock(return_value=make_skill())
    crud.update = AsyncMock(return_value=make_skill())
    crud.delete = AsyncMock(return_value=True)
    return crud


@pytest.fixture()
def mock_db():
    return AsyncMock()


@pytest.fixture()
def mock_storage():
    return MagicMock()


@pytest.fixture()
def client(mock_skill_crud, mock_db, mock_storage, patch_bootstrap):
    user = make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_structure_db] = lambda: mock_db
    app.dependency_overrides[get_skill_crud] = lambda: mock_skill_crud
    app.dependency_overrides[get_global_s3_storage] = lambda: mock_storage

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


BASE = "/api/agent/skills"


@pytest.mark.skip(reason="Create skill endpoint removed from router")
class TestCreateSkill:
    def test_create_success(self, client):
        pass

    def test_create_duplicate_name(self, client):
        pass

    def test_create_missing_fields(self, client):
        pass

    def test_create_process_failure(self, client):
        pass


class TestGetSkill:
    # get_skill uses SkillCRUD(db) directly, not Depends(get_skill_crud)
    def test_get_success(self, client):
        skill = make_skill()
        with patch("structure.routers.context.skills.SkillCRUD") as MockCRUD:
            MockCRUD.return_value.get_by_id = AsyncMock(return_value=skill)
            resp = client.get(f"{BASE}/{SKILL_ID}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Skill"

    def test_get_not_found(self, client):
        with patch("structure.routers.context.skills.SkillCRUD") as MockCRUD:
            MockCRUD.return_value.get_by_id = AsyncMock(return_value=None)
            resp = client.get(f"{BASE}/{uuid4()}")
        assert resp.status_code == 404


class TestUpdateSkill:
    # update_skill uses Depends(get_skill_crud) — handled by fixture
    def test_update_success(self, client):
        with patch("structure.celery_worker.tasks.context_sync_tasks.sync_skill_to_contexts") as mock_task:
            mock_task.delay = MagicMock()
            resp = client.put(
                f"{BASE}/{SKILL_ID}",
                json={"name": "Updated", "content": "# Updated"},
            )
        assert resp.status_code == 200

    def test_update_not_found(self, client, mock_skill_crud):
        mock_skill_crud.update = AsyncMock(return_value=None)
        resp = client.put(f"{BASE}/{uuid4()}", json={"name": "X"})
        assert resp.status_code == 404


class TestDeleteSkill:
    # delete_skill uses Depends(get_skill_crud) — handled by fixture
    def test_delete_success(self, client):
        with patch("structure.celery_worker.tasks.context_sync_tasks.delete_resource_contexts") as mock_task:
            mock_task.delay = MagicMock()
            resp = client.delete(f"{BASE}/{SKILL_ID}")
        assert resp.status_code == 204

    def test_delete_not_found(self, client, mock_skill_crud):
        mock_skill_crud.get_by_id = AsyncMock(return_value=None)
        resp = client.delete(f"{BASE}/{uuid4()}")
        assert resp.status_code == 404


class TestListSkills:
    # list_skills uses SkillCRUD(db) directly
    def test_list_success(self, client):
        skill = make_skill()
        with patch("structure.routers.context.skills.SkillCRUD") as MockCRUD:
            MockCRUD.return_value.list = AsyncMock(return_value=([skill], 1))
            resp = client.get(BASE)
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_list_with_tags(self, client):
        with patch("structure.routers.context.skills.SkillCRUD") as MockCRUD:
            MockCRUD.return_value.list = AsyncMock(return_value=([], 0))
            resp = client.get(f"{BASE}?tags=python,coding")
        assert resp.status_code == 200


class TestSearchSkills:
    # search_skills uses SkillCRUD(db) directly
    def test_search_success(self, client):
        skill = make_skill()
        with patch("structure.routers.context.skills.SkillCRUD") as MockCRUD:
            MockCRUD.return_value.search = AsyncMock(return_value=([skill], 1))
            resp = client.get(f"{BASE}/search/query?q=test")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_search_missing_query(self, client):
        resp = client.get(f"{BASE}/search/query")
        assert resp.status_code == 422


class TestProcessSkill:
    # process_skill uses SkillCRUD(db) directly
    def test_process_success(self, client):
        skill = make_skill()
        with (
            patch("structure.routers.context.skills.SkillCRUD") as MockCRUD,
            patch("structure.celery_worker.tasks.context_sync_tasks.sync_skill_to_contexts") as mock_task,
        ):
            MockCRUD.return_value.get_by_id = AsyncMock(return_value=skill)
            mock_task.delay = MagicMock()
            resp = client.post(f"{BASE}/{SKILL_ID}/process")
        assert resp.status_code == 200

    def test_process_not_found(self, client):
        with patch("structure.routers.context.skills.SkillCRUD") as MockCRUD:
            MockCRUD.return_value.get_by_id = AsyncMock(return_value=None)
            resp = client.post(f"{BASE}/{uuid4()}/process")
        assert resp.status_code == 404
