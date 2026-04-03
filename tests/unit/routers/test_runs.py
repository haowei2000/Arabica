"""Unit tests for /api/workspaces/{workspace_id}/runs/* endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from structure.app import app
from structure.core.dependencies.auth import get_current_user
from structure.core.dependencies.workspace import (
    get_event_publisher,
    get_run_crud,
    get_run_state_machine,
    get_workspace_crud,
)
from structure.core.enums.runs import RunStatus, TriggerType
from structure.extensions.database import get_structure_db
from tests.unit.routers.conftest import (
    RUN_ID,
    USER_ID,
    WORKSPACE_ID,
    make_user,
)


def make_run(**kwargs):
    now = datetime.now(UTC)
    r = MagicMock()
    r.id = str(RUN_ID)
    r.workspace_id = str(WORKSPACE_ID)
    r.app_id = None
    r.user_id = str(USER_ID)
    r.parent_run_id = None
    r.status = RunStatus.PENDING.value
    r.trigger_type = TriggerType.USER.value
    r.input_data = None
    r.output_data = None
    r.error = None
    r.error_code = None
    r.waiting_for = None
    r.last_event_sequence = 0
    r.input_tokens = 0
    r.output_tokens = 0
    r.legacy_task_id = None
    r.started_at = None
    r.completed_at = None
    r.created_at = now
    r.updated_at = now
    r.executor_code = None
    for k, v in kwargs.items():
        setattr(r, k, v)
    return r


def make_workspace():
    ws = MagicMock()
    ws.id = str(WORKSPACE_ID)
    ws.executor_code = None
    return ws


@pytest.fixture()
def mock_workspace_crud():
    crud = AsyncMock()
    crud.get_by_id_and_user = AsyncMock(return_value=make_workspace())
    return crud


@pytest.fixture()
def mock_run_crud():
    crud = AsyncMock()
    crud.create = AsyncMock(return_value=make_run())
    crud.get_by_id = AsyncMock(return_value=make_run())
    crud.get_by_id_and_user = AsyncMock(return_value=make_run())
    crud.list_by_workspace = AsyncMock(return_value=([make_run()], 1))
    crud.list_by_user = AsyncMock(return_value=([make_run()], 1))
    return crud


@pytest.fixture()
def mock_state_machine():
    sm = AsyncMock()
    sm.db = AsyncMock()
    sm.redis = None
    sm.start = AsyncMock(return_value=make_run(status=RunStatus.RUNNING.value))
    sm.cancel = AsyncMock(return_value=make_run(status=RunStatus.CANCELLED.value))
    sm.resume_from_tool = AsyncMock(return_value=make_run(status=RunStatus.RUNNING.value))
    return sm


@pytest.fixture()
def mock_event_publisher():
    pub = AsyncMock()
    pub.publish = AsyncMock()
    return pub


@pytest.fixture()
def mock_db():
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


@pytest.fixture()
def client(mock_workspace_crud, mock_run_crud, mock_state_machine, mock_event_publisher, mock_db, patch_bootstrap):
    user = make_user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_structure_db] = lambda: mock_db
    app.dependency_overrides[get_workspace_crud] = lambda: mock_workspace_crud
    app.dependency_overrides[get_run_crud] = lambda: mock_run_crud
    app.dependency_overrides[get_run_state_machine] = lambda: mock_state_machine
    app.dependency_overrides[get_event_publisher] = lambda: mock_event_publisher

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


WS_RUNS = f"/api/workspaces/{WORKSPACE_ID}/runs"


# ── POST /api/workspaces/{ws}/runs ────────────────────────────────


class TestCreateRun:
    def test_create_success(self, client):
        resp = client.post(
            WS_RUNS,
            json={
                "workspace_id": str(WORKSPACE_ID),
                "payload": {"message": "Hello"},
            },
        )
        assert resp.status_code == 201
        assert resp.json()["id"] == str(RUN_ID)

    def test_create_workspace_not_found(self, client, mock_workspace_crud):
        mock_workspace_crud.get_by_id_and_user.return_value = None
        resp = client.post(
            WS_RUNS,
            json={
                "workspace_id": str(WORKSPACE_ID),
                "payload": {"message": "Hello"},
            },
        )
        assert resp.status_code == 404

    def test_create_missing_message(self, client):
        resp = client.post(
            WS_RUNS,
            json={"workspace_id": str(WORKSPACE_ID)},
        )
        assert resp.status_code == 422


# ── GET /api/workspaces/{ws}/runs/{run_id} ────────────────────────


class TestGetRun:
    def test_get_success(self, client):
        resp = client.get(f"{WS_RUNS}/{RUN_ID}")
        assert resp.status_code == 200
        assert resp.json()["id"] == str(RUN_ID)

    def test_get_workspace_not_found(self, client, mock_workspace_crud):
        mock_workspace_crud.get_by_id_and_user.return_value = None
        resp = client.get(f"{WS_RUNS}/{RUN_ID}")
        assert resp.status_code == 404

    def test_get_run_not_found(self, client, mock_run_crud):
        mock_run_crud.get_by_id.return_value = None
        resp = client.get(f"{WS_RUNS}/{uuid4()}")
        assert resp.status_code == 404

    def test_get_run_wrong_workspace(self, client, mock_run_crud):
        run = make_run(workspace_id=str(uuid4()))  # different workspace
        mock_run_crud.get_by_id.return_value = run
        resp = client.get(f"{WS_RUNS}/{RUN_ID}")
        assert resp.status_code == 404


# ── GET /api/workspaces/{ws}/runs ─────────────────────────────────


class TestListRuns:
    def test_list_success(self, client):
        resp = client.get(WS_RUNS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1

    def test_list_with_status_filter(self, client):
        resp = client.get(f"{WS_RUNS}?status=running")
        assert resp.status_code == 200


# ── POST /api/workspaces/{ws}/runs/{run_id}/cancel ────────────────


class TestCancelRun:
    def test_cancel_success(self, client):
        resp = client.post(f"{WS_RUNS}/{RUN_ID}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == RunStatus.CANCELLED.value

    def test_cancel_run_not_found(self, client, mock_run_crud):
        mock_run_crud.get_by_id.return_value = None
        resp = client.post(f"{WS_RUNS}/{uuid4()}/cancel")
        assert resp.status_code == 404

    def test_cancel_invalid_state(self, client, mock_state_machine):
        mock_state_machine.cancel.side_effect = Exception("Cannot cancel a finished run")
        resp = client.post(f"{WS_RUNS}/{RUN_ID}/cancel")
        assert resp.status_code == 400


# ── POST /api/workspaces/{ws}/runs/{run_id}/resume ────────────────


class TestResumeRun:
    def test_resume_success(self, client, mock_run_crud):
        run = make_run(status=RunStatus.WAITING.value, waiting_for={"executor_code": "SimpleAgent"})
        mock_run_crud.get_by_id.return_value = run

        resp = client.post(
            f"{WS_RUNS}/{RUN_ID}/resume",
            json={"approval": True},
        )
        assert resp.status_code == 200

    def test_resume_not_waiting(self, client, mock_run_crud):
        run = make_run(status=RunStatus.RUNNING.value)
        mock_run_crud.get_by_id.return_value = run

        resp = client.post(
            f"{WS_RUNS}/{RUN_ID}/resume",
            json={"approval": True},
        )
        assert resp.status_code == 400

    def test_resume_run_not_found(self, client, mock_run_crud):
        mock_run_crud.get_by_id.return_value = None
        resp = client.post(f"{WS_RUNS}/{uuid4()}/resume", json={})
        assert resp.status_code == 404


# ── POST /api/workspaces/{ws}/runs/{run_id}/feedback ─────────────


class TestFeedback:
    def test_feedback_success(self, client, mock_run_crud):
        run = make_run(
            status=RunStatus.WAITING.value,
            waiting_for={"type": "user_input", "tool_name": "ask_for_user"},
        )
        mock_run_crud.get_by_id.return_value = run

        resp = client.post(
            f"{WS_RUNS}/{RUN_ID}/feedback",
            json={"feedback": "Yes, proceed."},
        )
        assert resp.status_code == 200

    def test_feedback_run_not_waiting(self, client, mock_run_crud):
        run = make_run(status=RunStatus.RUNNING.value)
        mock_run_crud.get_by_id.return_value = run

        resp = client.post(
            f"{WS_RUNS}/{RUN_ID}/feedback",
            json={"feedback": "Yes"},
        )
        assert resp.status_code == 400

    def test_feedback_not_waiting_for_user_input(self, client, mock_run_crud):
        run = make_run(
            status=RunStatus.WAITING.value,
            waiting_for={"type": "tool_approval"},
        )
        mock_run_crud.get_by_id.return_value = run

        resp = client.post(
            f"{WS_RUNS}/{RUN_ID}/feedback",
            json={"feedback": "ok"},
        )
        assert resp.status_code == 400


# ── Standalone runs router ────────────────────────────────────────


class TestStandaloneRuns:
    def test_get_run_by_id(self, client):
        resp = client.get(f"/api/runs/{RUN_ID}")
        assert resp.status_code == 200

    def test_get_run_not_found(self, client, mock_run_crud):
        mock_run_crud.get_by_id_and_user.return_value = None
        resp = client.get(f"/api/runs/{uuid4()}")
        assert resp.status_code == 404

    def test_list_user_runs(self, client):
        resp = client.get("/api/runs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
