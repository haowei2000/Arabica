"""Unit tests for /api/quota/* endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
import pytest

from structure.app import app
from structure.core.dependencies.auth import get_current_user
from structure.core.dependencies.workspace import get_quota_service
from tests.unit.routers.conftest import make_user


@pytest.fixture()
def mock_quota_service():
    quota = MagicMock()
    quota.free_quota_total = 100_000
    quota.free_quota_used = 25_000
    quota.paid_quota_total = 0
    quota.paid_quota_used = 0
    quota.remaining_tokens = 75_000
    quota.period_start = None
    quota.period_end = None

    service = AsyncMock()
    service.ensure_user_quota = AsyncMock(return_value=quota)
    return service


@pytest.fixture()
def client(mock_quota_service, patch_bootstrap):
    user = make_user(email_verified=True, email_verified_at=datetime.now(UTC))
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_quota_service] = lambda: mock_quota_service

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


def test_get_my_quota(client, mock_quota_service):
    resp = client.get("/api/quota/me")

    assert resp.status_code == 200
    assert resp.json()["free_quota_total"] == 100_000
    assert resp.json()["free_quota_used"] == 25_000
    assert resp.json()["remaining_tokens"] == 75_000
    mock_quota_service.ensure_user_quota.assert_awaited_once()
