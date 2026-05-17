"""Unit tests for artifact download behavior."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from structure.app import app
from structure.core.dependencies.auth import get_current_user
from structure.core.dependencies.workspace import get_workspace_crud
from structure.extensions.storage.global_storage import get_global_s3_storage
from structure.routers.runs.artifacts import get_artifact_crud
from tests.unit.routers.conftest import RUN_ID, WORKSPACE_ID, make_user


def make_workspace():
    ws = MagicMock()
    ws.id = str(WORKSPACE_ID)
    return ws


def make_artifact(**kwargs):
    now = datetime.now(UTC)
    artifact = MagicMock()
    artifact.id = str(uuid4())
    artifact.workspace_id = str(WORKSPACE_ID)
    artifact.run_id = str(RUN_ID)
    artifact.name = "report.md"
    artifact.artifact_type = "document"
    artifact.content_type = "text/markdown"
    artifact.content = "# Report"
    artifact.s3_key = None
    artifact.s3_url = None
    artifact.version = 1
    artifact.meta = {}
    artifact.created_at = now
    artifact.updated_at = now
    for key, value in kwargs.items():
        setattr(artifact, key, value)
    return artifact


@pytest.fixture()
def mock_workspace_crud():
    crud = AsyncMock()
    crud.get_by_id_and_user = AsyncMock(return_value=make_workspace())
    return crud


@pytest.fixture()
def mock_artifact_crud():
    crud = AsyncMock()
    crud.get_by_id = AsyncMock(return_value=make_artifact())
    return crud


@pytest.fixture()
def mock_storage():
    storage = MagicMock()
    storage.get_bytes = MagicMock(return_value=b"s3 artifact")
    return storage


@pytest.fixture()
def client(mock_workspace_crud, mock_artifact_crud, mock_storage, patch_bootstrap):
    app.dependency_overrides[get_current_user] = lambda: make_user()
    app.dependency_overrides[get_workspace_crud] = lambda: mock_workspace_crud
    app.dependency_overrides[get_artifact_crud] = lambda: mock_artifact_crud
    app.dependency_overrides[get_global_s3_storage] = lambda: mock_storage

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


def test_download_artifact_prefers_s3_key(client, mock_artifact_crud, mock_storage):
    artifact_id = uuid4()
    mock_artifact_crud.get_by_id.return_value = make_artifact(
        id=str(artifact_id),
        s3_key="artifacts/ws/report",
        s3_url="http://storage.example/report",
        content="inline fallback",
    )

    resp = client.get(
        f"/api/workspaces/{WORKSPACE_ID}/artifacts/{artifact_id}/download"
    )

    assert resp.status_code == 200
    assert resp.content == b"s3 artifact"
    mock_storage.get_bytes.assert_called_once_with("artifacts/ws/report")
