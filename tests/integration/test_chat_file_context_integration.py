"""Integration tests for chat file workspace-context behavior.

These tests exercise the actual FastAPI routes, SQLAlchemy ORM tables, and
``read_context`` tool path.  PostgreSQL is required because the context models
use PostgreSQL/pgvector-specific types.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote_plus
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from structure.core.dependencies.auth import get_current_user
from structure.extensions.database import get_structure_db
from structure.extensions.storage.base import ObjectInfo
from structure.extensions.storage.global_storage import get_global_s3_storage
from structure.models.app.app import App
from structure.models.context.context import Context
from structure.models.context.workspace_context import WorkspaceContext
from structure.models.workspaces.workspace import Workspace
from structure.models.workspaces.workspace_member import WorkspaceMember
from structure.plugins.tools.context.read_context import ReadContextTool
from structure.schemas.auth.user import UserResponse
from structure.utils.workspace_context_cache import clear_workspace_context_cache

pytestmark = pytest.mark.integration


_DEFAULT_DB_URL = (
    "postgresql+asyncpg://test_user:test_password@localhost:5432/structure_test"
)


def _project_env_values() -> dict[str, str]:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _integration_db_url() -> str:
    if explicit_url := os.getenv("STRUCTURE_INTEGRATION_DATABASE_URL"):
        return explicit_url

    project_env = _project_env_values()
    if not project_env:
        return _DEFAULT_DB_URL

    username = quote_plus(project_env.get("POSTGRES__USERNAME", "test_user"))
    password = quote_plus(project_env.get("POSTGRES__PASSWORD", "test_password"))
    host = project_env.get("POSTGRES__HOST", "localhost")
    port = project_env.get(
        "POSTGRES__PORT",
        project_env.get("EXPOSE_PORT_POSTGRES", "5432"),
    )
    database = project_env.get("POSTGRES__STRUCTURE_DBNAME", "structure_test")
    return f"postgresql+asyncpg://{username}:{password}@{host}:{port}/{database}"


class MemoryStorage:
    """Minimal storage backend for route-level integration tests."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str | None, dict | None]] = {}

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: dict | None = None,
        overwrite: bool = True,
    ) -> ObjectInfo:
        if not overwrite and key in self.objects:
            raise FileExistsError(key)
        self.objects[key] = (data, content_type, metadata)
        return self.stat(key)

    def put_file(
        self,
        key: str,
        file: BinaryIO,
        *,
        content_type: str | None = None,
        metadata: dict | None = None,
        overwrite: bool = True,
    ) -> ObjectInfo:
        if not overwrite and key in self.objects:
            raise FileExistsError(key)
        position = file.tell()
        file.seek(0)
        self.objects[key] = (file.read(), content_type, metadata)
        file.seek(position)
        return self.stat(key)

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key][0]

    def open(self, key: str) -> BinaryIO:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self.objects

    def stat(self, key: str) -> ObjectInfo:
        data, content_type, metadata = self.objects[key]
        return ObjectInfo(
            key=key,
            size=len(data),
            etag=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
            last_modified=datetime.now(UTC),
            metadata=metadata,
        )

    def list(self, prefix: str = "") -> Iterable[ObjectInfo]:
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield self.stat(key)


def _make_user(user_id: UUID) -> UserResponse:
    now = datetime.now(UTC)
    return UserResponse(
        id=user_id,
        username="chat-file-it",
        email="chat-file-it@example.com",
        phone=None,
        tenant_id=uuid4(),
        role="user",
        is_active=True,
        is_superuser=False,
        email_verified=True,
        email_verified_at=now,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture()
async def chat_file_sessionmaker() -> AsyncGenerator[
    async_sessionmaker[AsyncSession],
    None,
]:
    db_url = _integration_db_url()
    schema = f"chat_file_it_{uuid4().hex}"
    bootstrap_engine = create_async_engine(db_url, pool_pre_ping=False)

    try:
        async with bootstrap_engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:
        await bootstrap_engine.dispose()
        pytest.skip(f"PostgreSQL integration database is unavailable: {exc}")

    raw_engine = create_async_engine(db_url, pool_pre_ping=False)
    engine = raw_engine.execution_options(schema_translate_map={None: schema})
    tables = [
        App.__table__,
        Workspace.__table__,
        WorkspaceMember.__table__,
        Context.__table__,
        WorkspaceContext.__table__,
    ]

    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Workspace.metadata.create_all(
                    sync_conn,
                    tables=tables,
                )
            )

        yield async_sessionmaker(
            engine,
            expire_on_commit=False,
            autoflush=False,
        )
    finally:
        clear_workspace_context_cache()
        await raw_engine.dispose()
        async with bootstrap_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await bootstrap_engine.dispose()


@pytest.fixture()
async def seeded_workspace(
    chat_file_sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID]:
    user_id = uuid4()
    workspace_id = uuid4()

    async with chat_file_sessionmaker() as session:
        session.add(
            Workspace(
                id=workspace_id,
                name="Chat file integration workspace",
                owner_id=user_id,
                settings={},
            )
        )
        session.add(
            WorkspaceMember(
                workspace_id=workspace_id,
                user_id=user_id,
                role="owner",
                invitation_status="accepted",
                joined_at=datetime.now(UTC),
            )
        )
        await session.commit()

    return user_id, workspace_id


@pytest.fixture()
async def chat_file_client(
    chat_file_sessionmaker: async_sessionmaker[AsyncSession],
    seeded_workspace: tuple[UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[httpx.AsyncClient, MemoryStorage, UUID, UUID], None]:
    user_id, workspace_id = seeded_workspace
    storage = MemoryStorage()

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        async with chat_file_sessionmaker() as session:
            yield session
            await session.commit()

    async def noop_invalidate(_workspace_ids: list[str]) -> None:
        return None

    from structure.app import app

    monkeypatch.setattr(
        "structure.routers.workspaces.chat_files._invalidate_workspace_caches",
        noop_invalidate,
    )

    app.dependency_overrides[get_current_user] = lambda: _make_user(user_id)
    app.dependency_overrides[get_structure_db] = override_db
    app.dependency_overrides[get_global_s3_storage] = lambda: storage

    clear_workspace_context_cache()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client, storage, user_id, workspace_id

    app.dependency_overrides.clear()
    clear_workspace_context_cache()


async def test_chat_file_upload_context_read_and_download_round_trip(
    chat_file_client: tuple[httpx.AsyncClient, MemoryStorage, UUID, UUID],
    chat_file_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
):
    client, storage, user_id, workspace_id = chat_file_client
    file_bytes = b"uploaded integration file\nsecond line"

    upload_resp = await client.post(
        f"/api/workspaces/{workspace_id}/chat-files",
        files=[("files[]", ("integration-note.txt", file_bytes, "text/plain"))],
    )
    assert upload_resp.status_code == 201
    uploaded = upload_resp.json()["items"][0]
    assert uploaded["name"] == "integration-note.txt"
    assert uploaded["path"].startswith("/chat/uploads/")
    assert uploaded["download_url"].endswith("/download")

    async with chat_file_sessionmaker() as session:
        row = await session.scalar(
            select(WorkspaceContext).where(WorkspaceContext.id == UUID(uploaded["id"]))
        )
        assert row is not None
        assert row.workspace_id == workspace_id
        assert row.created_by == user_id
        assert row.path == uploaded["path"]
        assert row.tags == ["chat", "upload", "file"]
        assert row.s3_key in storage.objects
        assert storage.objects[row.s3_key][0] == file_bytes
        assert row.meta["sha256"] == hashlib.sha256(file_bytes).hexdigest()
        assert row.meta["download_url"] == uploaded["download_url"]
        assert row.content and "uploaded integration file" in row.content

    list_resp = await client.get(f"/api/workspaces/{workspace_id}/contexts")
    assert list_resp.status_code == 200
    listed_items = list_resp.json()["items"]
    assert any(item["id"] == uploaded["id"] for item in listed_items)

    @asynccontextmanager
    async def integration_get_session(
        _bind_name: str = "structure",
    ) -> AsyncGenerator[AsyncSession, None]:
        async with chat_file_sessionmaker() as session:
            yield session

    clear_workspace_context_cache()
    monkeypatch.setattr(
        "structure.extensions.database.get_session", integration_get_session
    )
    read_result = await ReadContextTool().execute(
        ReadContextTool.InputSchema(
            workspace_id=str(workspace_id),
            path=uploaded["path"],
        )
    )
    assert read_result.success is True
    assert read_result.data is not None
    assert read_result.data["path"] == uploaded["path"].strip("/")
    assert "uploaded integration file" in read_result.data["content"]
    assert read_result.data["meta"]["source"] == "chat_upload"

    download_resp = await client.get(
        f"/api/workspaces/{workspace_id}/chat-files/{uploaded['id']}/download"
    )
    assert download_resp.status_code == 200
    assert download_resp.content == file_bytes
    assert download_resp.headers["content-type"].startswith("text/plain")
