"""Unit tests for /api/auth/* endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import pytest

from structure.app import app
from structure.core.dependencies.auth import get_current_user
from structure.extensions.database import get_structure_db
from structure.routers.auth.auth import get_email_service
from structure.services.auth.auth_service import AuthService
from tests.unit.routers.conftest import TENANT_ID, make_user


@pytest.fixture()
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture()
def mock_email_service():
    service = MagicMock()
    service.send_verification_email = AsyncMock(return_value=True)
    return service


@pytest.fixture()
def client(mock_db, patch_bootstrap, mock_email_service):
    app.dependency_overrides[get_structure_db] = lambda: mock_db
    app.dependency_overrides[get_email_service] = lambda: mock_email_service
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.pop(get_structure_db, None)
    app.dependency_overrides.pop(get_email_service, None)


def attach_service_db(service):
    service.db = MagicMock()
    service.db.commit = AsyncMock()
    service.db.rollback = AsyncMock()
    service.db.refresh = AsyncMock()
    return service.db


# ── AuthService ─────────────────────────────────────────────────


class TestAuthService:
    @pytest.mark.asyncio
    async def test_get_user_by_email_uses_case_insensitive_lookup(self):
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute.return_value = result

        service = AuthService(db)
        await service.get_user_by_email("Mixed@Example.COM")

        stmt = db.execute.await_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
        assert "lower(" in compiled
        assert "auth_user.email" in compiled
        assert "mixed@example.com" in compiled


# ── /api/auth/register ──────────────────────────────────────────


class TestRegister:
    def test_register_success(self, client):
        user = make_user()
        tenant = MagicMock(id=TENANT_ID)

        with (
            patch("structure.routers.auth.auth.AuthService") as MockService,
        ):
            svc = MockService.return_value
            service_db = attach_service_db(svc)
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
        svc.create_user.assert_awaited_once()
        assert svc.create_user.await_args.kwargs["auto_commit"] is False
        service_db.commit.assert_awaited_once()

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

    def test_register_rejects_when_required_verification_not_sent(
        self, client, mock_email_service
    ):
        mock_email_service.send_verification_email.return_value = False
        user = make_user(email="new@example.com")
        tenant = MagicMock(id=TENANT_ID)

        with patch("structure.routers.auth.auth.AuthService") as MockService:
            svc = MockService.return_value
            service_db = attach_service_db(svc)
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

        assert resp.status_code == 503
        assert "could not be sent" in resp.json()["detail"]
        service_db.rollback.assert_awaited_once()
        service_db.commit.assert_not_awaited()

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

    def test_register_by_email_success(self, client, mock_email_service):
        user = make_user(username="newuser", email="new@example.com")
        tenant = MagicMock(id=TENANT_ID)

        with patch("structure.routers.auth.auth.AuthService") as MockService:
            svc = MockService.return_value
            service_db = attach_service_db(svc)
            svc.get_user_by_email = AsyncMock(return_value=None)
            svc.get_tenant_by_name = AsyncMock(return_value=tenant)
            svc.create_user_by_email = AsyncMock(return_value=user)

            resp = client.post(
                "/api/auth/register/email",
                json={"email": "new@example.com", "password": "securepass"},
            )

        svc.create_user_by_email.assert_awaited_once_with(
            "new@example.com", "securepass", TENANT_ID, auto_commit=False
        )
        assert resp.status_code == 201
        assert resp.json()["email"] == "new@example.com"
        mock_email_service.send_verification_email.assert_awaited_once()
        service_db.commit.assert_awaited_once()

    def test_register_by_email_duplicate_email(self, client):
        existing = make_user()

        with patch("structure.routers.auth.auth.AuthService") as MockService:
            svc = MockService.return_value
            svc.get_user_by_email = AsyncMock(return_value=existing)

            resp = client.post(
                "/api/auth/register/email",
                json={"email": "test@example.com", "password": "securepass"},
            )

        assert resp.status_code == 400
        assert "already registered" in resp.json()["detail"]

    def test_register_by_email_rejects_when_required_verification_not_sent(
        self, client, mock_email_service
    ):
        mock_email_service.send_verification_email.return_value = False
        user = make_user(username="newuser", email="new@example.com")
        tenant = MagicMock(id=TENANT_ID)

        with patch("structure.routers.auth.auth.AuthService") as MockService:
            svc = MockService.return_value
            service_db = attach_service_db(svc)
            svc.get_user_by_email = AsyncMock(return_value=None)
            svc.get_tenant_by_name = AsyncMock(return_value=tenant)
            svc.create_user_by_email = AsyncMock(return_value=user)

            resp = client.post(
                "/api/auth/register/email",
                json={"email": "new@example.com", "password": "securepass"},
            )

        assert resp.status_code == 503
        assert "could not be sent" in resp.json()["detail"]
        service_db.rollback.assert_awaited_once()
        service_db.commit.assert_not_awaited()


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

    def test_login_unverified_email(self, client):
        user = make_user(email_verified=False, email_verified_at=None)

        with (
            patch("structure.routers.auth.auth.AuthService") as MockService,
            patch("structure.routers.auth.auth.get_settings") as mock_get_settings,
        ):
            mock_get_settings.return_value.auth.require_email_verification = True
            svc = MockService.return_value
            svc.authenticate_user = AsyncMock(return_value=user)

            resp = client.post(
                "/api/auth/login",
                data={"username": "test@example.com", "password": "securepass"},
            )

        assert resp.status_code == 403
        assert "not verified" in resp.json()["detail"]


# ── /api/auth/verify-email ──────────────────────────────────────


class TestEmailVerification:
    def test_verify_email_success(self, client):
        user = make_user()

        with (
            patch("structure.routers.auth.auth.AuthService") as MockService,
            patch("structure.routers.auth.auth.TokenService") as MockTokenService,
        ):
            MockTokenService.verify_email_verification_token.return_value = {
                "user_id": str(user.id),
                "email": user.email,
            }
            svc = MockService.return_value
            svc.mark_email_verified = AsyncMock(return_value=user)

            resp = client.get("/api/auth/verify-email?token=valid-token")

        assert resp.status_code == 200
        assert resp.json()["verified"] is True
        svc.mark_email_verified.assert_awaited_once()

    def test_verify_email_invalid_token(self, client):
        with patch("structure.routers.auth.auth.TokenService") as MockTokenService:
            MockTokenService.verify_email_verification_token.return_value = None

            resp = client.get("/api/auth/verify-email?token=bad-token")

        assert resp.status_code == 400

    def test_resend_verification_email(self, client, mock_email_service):
        user = make_user(email_verified=False, email_verified_at=None)

        with patch("structure.routers.auth.auth.AuthService") as MockService:
            svc = MockService.return_value
            svc.get_user_by_email = AsyncMock(return_value=user)

            resp = client.post(
                "/api/auth/verify-email/resend",
                json={"email": "test@example.com"},
            )

        assert resp.status_code == 200
        assert resp.json()["verified"] is False
        mock_email_service.send_verification_email.assert_awaited_once()


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
