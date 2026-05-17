"""Unit tests for /api/llm/* model endpoints."""

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
import pytest

from structure.app import app
from structure.core.dependencies.agents import (
    get_chat_model_crud,
    get_embedding_model_crud,
)
from structure.core.dependencies.auth import get_current_user
from tests.unit.routers.conftest import make_user


@pytest.fixture()
def client(patch_bootstrap, monkeypatch):
    monkeypatch.setenv("EMBED_WORKER", "false")
    app.dependency_overrides[get_current_user] = lambda: make_user()

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


def test_non_superuser_cannot_update_system_chat_model(client):
    crud = MagicMock()
    system_model = MagicMock()
    system_model.is_system = True
    system_model.user_id = None
    crud.get_by_id = AsyncMock(return_value=system_model)
    crud.update = AsyncMock()
    app.dependency_overrides[get_chat_model_crud] = lambda: crud

    response = client.put(
        "/api/llm/chat-models/00000000-0000-0000-0000-000000000001",
        json={"name": "Changed"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Cannot modify system models"
    crud.update.assert_not_awaited()


def test_non_superuser_cannot_update_system_embedding_model(client):
    crud = MagicMock()
    system_model = MagicMock()
    system_model.is_system = True
    system_model.user_id = None
    crud.get_by_id = AsyncMock(return_value=system_model)
    crud.update = AsyncMock()
    app.dependency_overrides[get_embedding_model_crud] = lambda: crud

    response = client.put(
        "/api/llm/embedding-models/00000000-0000-0000-0000-000000000001",
        json={"name": "Changed"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Cannot modify system models"
    crud.update.assert_not_awaited()
