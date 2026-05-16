"""Unit tests for workspace chat-file endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from structure.app import app
from structure.core.dependencies.auth import get_current_user
from structure.core.dependencies.workspace import get_workspace_crud
from structure.extensions.database import get_structure_db
from structure.extensions.storage.global_storage import get_global_s3_storage
from structure.models.context.workspace_context import WorkspaceContext
from tests.unit.routers.conftest import USER_ID, WORKSPACE_ID, make_user


def make_workspace():
    ws = MagicMock()
    ws.id = str(WORKSPACE_ID)
    return ws


@pytest.fixture()
def mock_workspace_crud():
    crud = AsyncMock()
    crud.get_by_id_and_user = AsyncMock(return_value=make_workspace())
    return crud


@pytest.fixture()
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.fixture()
def mock_storage():
    storage = MagicMock()
    storage.put_bytes = MagicMock()
    storage.put_file = MagicMock()
    storage.get_bytes = MagicMock(return_value=b"downloaded bytes")
    return storage


@pytest.fixture()
def client(mock_workspace_crud, mock_db, mock_storage, patch_bootstrap):
    app.dependency_overrides[get_current_user] = lambda: make_user()
    app.dependency_overrides[get_workspace_crud] = lambda: mock_workspace_crud
    app.dependency_overrides[get_structure_db] = lambda: mock_db
    app.dependency_overrides[get_global_s3_storage] = lambda: mock_storage

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


def test_upload_chat_file_creates_workspace_context(client, mock_db, mock_storage):
    with (
        patch(
            "structure.routers.workspaces.chat_files.DocumentParser.parse",
            return_value="# Parsed\nhello",
        ),
        patch(
            "structure.routers.workspaces.chat_files._invalidate_workspace_caches",
            new=AsyncMock(),
        ),
    ):
        resp = client.post(
            f"/api/workspaces/{WORKSPACE_ID}/chat-files",
            files=[("files[]", ("note.txt", b"hello", "text/plain"))],
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["name"] == "note.txt"
    assert item["path"].startswith("/chat/uploads/")
    assert item["parse_status"] == "parsed"
    assert item["download_url"].endswith("/download")

    mock_storage.put_file.assert_called_once()
    mock_storage.put_bytes.assert_not_called()
    ws_context = mock_db.add.call_args.args[0]
    assert ws_context.workspace_id == WORKSPACE_ID
    assert ws_context.created_by == USER_ID
    assert ws_context.content == "# Parsed\nhello"
    assert ws_context.s3_key.startswith(f"workspaces/{WORKSPACE_ID}/chat_uploads/")
    assert ws_context.tags == ["chat", "upload", "file"]


def test_upload_chat_file_rejects_empty_file(client):
    resp = client.post(
        f"/api/workspaces/{WORKSPACE_ID}/chat-files",
        files=[("files[]", ("empty.txt", b"", "text/plain"))],
    )

    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_upload_chat_file_checks_workspace_access(
    client, mock_workspace_crud, mock_storage
):
    mock_workspace_crud.get_by_id_and_user.return_value = None

    resp = client.post(
        f"/api/workspaces/{WORKSPACE_ID}/chat-files",
        files=[("files[]", ("note.txt", b"hello", "text/plain"))],
    )

    assert resp.status_code == 404
    mock_storage.put_bytes.assert_not_called()


def test_upload_chat_file_marks_parse_failure(client, mock_db):
    with (
        patch(
            "structure.routers.workspaces.chat_files.DocumentParser.parse",
            side_effect=ValueError("bad file"),
        ),
        patch(
            "structure.routers.workspaces.chat_files._invalidate_workspace_caches",
            new=AsyncMock(),
        ),
    ):
        resp = client.post(
            f"/api/workspaces/{WORKSPACE_ID}/chat-files",
            files=[("files[]", ("broken.txt", b"hello", "text/plain"))],
        )

    assert resp.status_code == 201
    assert resp.json()["items"][0]["parse_status"] == "failed"
    ws_context = mock_db.add.call_args.args[0]
    assert ws_context.content is None
    assert ws_context.meta["parse_error"] == "bad file"


def test_download_chat_file_returns_original_bytes(client, mock_db, mock_storage):
    context_id = uuid4()
    now = datetime.now(UTC)
    row = WorkspaceContext(
        id=context_id,
        workspace_id=WORKSPACE_ID,
        created_by=USER_ID,
        path=f"/chat/uploads/{context_id}/note.txt",
        name="note.txt",
        content_type="text/plain",
        s3_key="workspaces/ws/chat_uploads/file",
        size_bytes=16,
        meta={"original_name": "note.txt"},
        created_at=now,
        updated_at=now,
    )
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=row)
    mock_db.execute = AsyncMock(return_value=result)

    resp = client.get(
        f"/api/workspaces/{WORKSPACE_ID}/chat-files/{context_id}/download"
    )

    assert resp.status_code == 200
    assert resp.content == b"downloaded bytes"
    assert resp.headers["content-type"].startswith("text/plain")
    mock_storage.get_bytes.assert_called_once_with(row.s3_key)
