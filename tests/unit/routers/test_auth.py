"""Unit tests for /api/auth/* endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import pytest

from structure.app import app
from structure.core.dependencies.auth import get_current_user
from structure.extensions.database import get_structure_db
from tests.unit.routers.conftest import TENANT_ID, make_user


@pytest.fixture()
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture()
def client(mock_db, patch_bootstrap):
    app.dependency_overrides[get_structure_db] = lambda: mock_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.pop(get_structure_db, None)


# ── /api/auth/register ──────────────────────────────────────────


class TestRegister:
    def test_register_success(self, client):
        user = make_user()
        tenant = MagicMock(id=TENANT_ID)

        with (
            patch("structure.routers.auth.auth.AuthService") as MockService,
        ):
            svc = MockService.return_value
            svc.get_user_by_username = AsyncMock(return_value=None)
            svc.get_user_by_email = AsyncMock(return_value=None)
            svc.get_tenant_by_name = AsyncMock(return_value=tenant)
            svc.create_user = AsyncMock(return_value=user)

            resp = client.post(
                "/api/auth/register",
                json={
                    "username": "newuser",
                    "email": "new@example.com",
                    "password": "securepass",
                },
            )

        assert resp.status_code == 201
        assert resp.json()["username"] == "testuser"

    def test_register_duplicate_username(self, client):
        existing = make_user()

        with patch("structure.routers.auth.auth.AuthService") as MockService:
            svc = MockService.return_value
            svc.get_user_by_username = AsyncMock(return_value=existing)

            resp = client.post(
                "/api/auth/register",
                json={
                    "username": "testuser",
                    "email": "other@example.com",
                    "password": "securepass",
                },
            )

        assert resp.status_code == 400
        assert "already registered" in resp.json()["detail"]

    def test_register_duplicate_email(self, client):
        existing = make_user()

        with patch("structure.routers.auth.auth.AuthService") as MockService:
            svc = MockService.return_value
            svc.get_user_by_username = AsyncMock(return_value=None)
            svc.get_user_by_email = AsyncMock(return_value=existing)

            resp = client.post(
                "/api/auth/register",
                json={
                    "username": "newuser2",
                    "email": "test@example.com",
                    "password": "securepass",
                },
            )

        assert resp.status_code == 400
        assert "already registered" in resp.json()["detail"]

    def test_register_creates_default_tenant(self, client):
        user = make_user()
        tenant = MagicMock(id=TENANT_ID)

        with patch("structure.routers.auth.auth.AuthService") as MockService:
            svc = MockService.return_value
            svc.get_user_by_username = AsyncMock(return_value=None)
            svc.get_user_by_email = AsyncMock(return_value=None)
            svc.get_tenant_by_name = AsyncMock(return_value=None)  # no tenant exists
            svc.create_tenant = AsyncMock(return_value=tenant)
            svc.create_user = AsyncMock(return_value=user)

            resp = client.post(
                "/api/auth/register",
                json={"username": "newuser", "password": "securepass"},
            )

        svc.create_tenant.assert_awaited_once()
        assert resp.status_code == 201


# ── /api/auth/login ─────────────────────────────────────────────


class TestLogin:
    def test_login_success(self, client):
        user = make_user()

        with (
            patch("structure.routers.auth.auth.AuthService") as MockService,
            patch("structure.routers.auth.auth.TokenService") as MockToken,
        ):
            svc = MockService.return_value
            svc.authenticate_user = AsyncMock(return_value=user)
            MockToken.create_tokens.return_value = ("access_tok", "refresh_tok")

            resp = client.post(
                "/api/auth/login",
                data={"username": "testuser", "password": "securepass"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] == "access_tok"
        assert body["token_type"] == "bearer"

    def test_login_invalid_credentials(self, client):
        with patch("structure.routers.auth.auth.AuthService") as MockService:
            svc = MockService.return_value
            svc.authenticate_user = AsyncMock(return_value=None)

            resp = client.post(
                "/api/auth/login",
                data={"username": "bad", "password": "wrong"},
            )

        assert resp.status_code == 401


# ── /api/auth/refresh ────────────────────────────────────────────


class TestRefresh:
    def test_refresh_success(self, client):
        with patch("structure.routers.auth.auth.TokenService") as MockToken:
            MockToken.refresh_tokens.return_value = ("new_access", "new_refresh")

            resp = client.post(
                "/api/auth/refresh",
                json={"refresh_token": "old_refresh"},
            )

        assert resp.status_code == 200
        assert resp.json()["access_token"] == "new_access"

    def test_refresh_invalid_token(self, client):
        with patch("structure.routers.auth.auth.TokenService") as MockToken:
            MockToken.refresh_tokens.return_value = None

            resp = client.post(
                "/api/auth/refresh",
                json={"refresh_token": "bad_token"},
            )

        assert resp.status_code == 401


# ── /api/auth/me ─────────────────────────────────────────────────


class TestGetMe:
    def test_get_me_authenticated(self, client):
        user = make_user()
        app.dependency_overrides[get_current_user] = lambda: user

        resp = client.get("/api/auth/me")

        assert resp.status_code == 200
        assert resp.json()["username"] == "testuser"

        app.dependency_overrides.pop(get_current_user, None)

    def test_get_me_unauthenticated(self, client):
        # No auth override — token validation will fail
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401
