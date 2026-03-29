"""Shared fixtures for router unit tests."""

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from structure.core.dependencies.auth import get_current_user
from structure.extensions.database import get_structure_db
from structure.schemas.auth.user import UserResponse

# Fixed UUIDs for deterministic tests
USER_ID = UUID("00000000-0000-0000-0000-000000000001")
TENANT_ID = UUID("00000000-0000-0000-0000-000000000002")
WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000003")
RUN_ID = UUID("00000000-0000-0000-0000-000000000004")
SKILL_ID = UUID("00000000-0000-0000-0000-000000000005")
TOOL_ID = UUID("00000000-0000-0000-0000-000000000006")
KNOWLEDGE_ID = UUID("00000000-0000-0000-0000-000000000007")


def make_user(**kwargs) -> UserResponse:
    """Build a UserResponse for auth override."""
    now = datetime.now(UTC)
    defaults = dict(
        id=USER_ID,
        username="testuser",
        email="test@example.com",
        phone=None,
        tenant_id=TENANT_ID,
        role="user",
        is_active=True,
        is_superuser=False,
        created_at=now,
        updated_at=now,
    )
    defaults.update(kwargs)
    return UserResponse(**defaults)


@pytest.fixture()
def mock_db():
    """Return an AsyncMock database session."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()
    db.flush = AsyncMock()
    return db


@pytest.fixture()
def current_user() -> UserResponse:
    return make_user()


@pytest.fixture(scope="session", autouse=True)
def patch_bootstrap():
    """Patch bootstrap_api so TestClient lifespan doesn't connect to real services."""
    mock_bootstrap = MagicMock()
    mock_bootstrap.redis_client = MagicMock()
    mock_bootstrap.cleanup = AsyncMock()
    with patch("structure.core.lifespan.bootstrap_api", return_value=mock_bootstrap):
        yield
