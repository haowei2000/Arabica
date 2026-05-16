"""Workspace chat-file upload and download endpoints."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import logging
import mimetypes
import os
from pathlib import Path
from typing import Annotated, BinaryIO
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.celery_worker.tasks.workspace_context_sync import (
    _invalidate_workspace_caches,
)
from structure.core.dependencies.auth import get_current_user
from structure.core.dependencies.workspace import WorkspaceCRUDDep
from structure.extensions.database import get_structure_db
from structure.extensions.storage.global_storage import get_global_s3_storage
from structure.extensions.storage.s3_storage_backend import S3StorageBackend
from structure.models.context.workspace_context import WorkspaceContext
from structure.schemas.auth.user import UserResponse
from structure.schemas.workspaces.chat_file import (
    ChatFileResponse,
    ChatFileUploadResponse,
)
from structure.services.context.knowledge.parser import DocumentParser
from structure.utils.workspace_context_cache import invalidate_workspace_context_cache

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/chat-files",
    tags=["chat-files"],
)

MAX_CHAT_FILE_PARSE_BYTES = 8 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024


def _safe_filename(filename: str | None) -> str:
    name = Path(filename or "upload").name.strip().replace("\x00", "")
    return name[:180] or "upload"


def _content_type_for(file: UploadFile, filename: str) -> str:
    return (
        file.content_type
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )


def _is_parse_supported(content_type: str | None, filename: str) -> bool:
    extension = f".{filename.rsplit('.', 1)[-1].lower()}" if "." in filename else None
    return bool(
        (content_type and content_type.lower() in DocumentParser.SUPPORTED_MIME_TYPES)
        or (extension and extension in DocumentParser.SUPPORTED_EXTENSIONS)
    )


def _download_url(workspace_id: str, context_id: UUID | str) -> str:
    return f"/api/workspaces/{workspace_id}/chat-files/{context_id}/download"


def _stream_size(file: BinaryIO) -> int:
    position = file.tell()
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(position)
    return size


def _stream_sha256(file: BinaryIO) -> str:
    position = file.tell()
    file.seek(0)
    hasher = hashlib.sha256()
    while chunk := file.read(HASH_CHUNK_BYTES):
        hasher.update(chunk)
    file.seek(position)
    return hasher.hexdigest()


def _to_response(item: WorkspaceContext) -> ChatFileResponse:
    meta = item.meta or {}
    return ChatFileResponse(
        id=str(item.id),
        name=item.name,
        path=item.path or "",
        content_type=item.content_type,
        size_bytes=item.size_bytes,
        download_url=_download_url(str(item.workspace_id), item.id),
        parse_status=str(meta.get("parse_status") or "unknown"),
        created_at=item.created_at,
    )


async def _ensure_workspace(
    workspace_id: str,
    current_user: UserResponse,
    workspace_crud: WorkspaceCRUDDep,
):
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )
    return workspace


@router.post(
    "", response_model=ChatFileUploadResponse, status_code=status.HTTP_201_CREATED
)
async def upload_chat_files(
    workspace_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    workspace_crud: WorkspaceCRUDDep,
    db: Annotated[AsyncSession, Depends(get_structure_db)],
    storage: Annotated[S3StorageBackend, Depends(get_global_s3_storage)],
    files: list[UploadFile] | None = File(  # noqa: B008
        None,
        description="Files to upload into chat",
    ),
    bracket_files: list[UploadFile] | None = File(  # noqa: B008
        None,
        alias="files[]",
        description="Files to upload into chat",
    ),
):
    """Upload chat files into workspace-scoped structured context."""
    await _ensure_workspace(workspace_id, current_user, workspace_crud)

    uploads = [*(files or []), *(bracket_files or [])]
    if not uploads:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one file is required",
        )

    workspace_uuid = UUID(workspace_id)
    user_uuid = UUID(str(current_user.id))
    parser = DocumentParser()
    rows: list[WorkspaceContext] = []
    prepared_uploads: list[tuple[UploadFile, str, int, str]] = []

    for upload_file in uploads:
        original_name = _safe_filename(upload_file.filename)
        size_bytes = await asyncio.to_thread(_stream_size, upload_file.file)
        if size_bytes == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot upload empty file: {original_name}",
            )
        prepared_uploads.append(
            (
                upload_file,
                original_name,
                size_bytes,
                _content_type_for(upload_file, original_name),
            )
        )

    for upload_file, original_name, size_bytes, content_type in prepared_uploads:
        context_id = uuid4()
        object_key = (
            f"workspaces/{workspace_id}/chat_uploads/{context_id}/{original_name}"
        )
        file_hash = await asyncio.to_thread(_stream_sha256, upload_file.file)

        upload_file.file.seek(0)
        await asyncio.to_thread(
            storage.put_file,
            key=object_key,
            file=upload_file.file,
            content_type=content_type,
            metadata={
                "workspace_id": workspace_id,
                "context_id": str(context_id),
                "original_name": original_name,
                "sha256": file_hash,
            },
        )

        content: str | None = None
        parse_status = "skipped"
        parse_error: str | None = None
        if size_bytes > MAX_CHAT_FILE_PARSE_BYTES:
            parse_status = "skipped_large"
        elif _is_parse_supported(content_type, original_name):
            try:
                upload_file.file.seek(0)
                content = await asyncio.to_thread(
                    parser.parse,
                    upload_file.file,
                    content_type,
                    original_name,
                )
                parse_status = "parsed" if content.strip() else "skipped_empty_text"
            except Exception as exc:
                parse_status = "failed"
                parse_error = str(exc)
                logger.info(
                    "Failed to parse chat upload %s in workspace %s: %s",
                    original_name,
                    workspace_id,
                    exc,
                )

        path = f"/chat/uploads/{context_id}/{original_name}"
        now = datetime.now(UTC)
        row = WorkspaceContext(
            id=context_id,
            workspace_id=workspace_uuid,
            created_by=user_uuid,
            path=path,
            name=original_name,
            content_type=content_type,
            glance=f"Uploaded chat file: {original_name}",
            summary=(
                f"Chat upload {original_name}; parse_status={parse_status}; "
                f"size={size_bytes} bytes"
            ),
            content=content,
            s3_key=object_key,
            size_bytes=size_bytes,
            tags=["chat", "upload", "file"],
            meta={
                "source": "chat_upload",
                "workspace_id": workspace_id,
                "created_by": str(current_user.id),
                "original_name": original_name,
                "object_key": object_key,
                "sha256": file_hash,
                "parse_status": parse_status,
                "parse_error": parse_error,
                "download_url": _download_url(workspace_id, context_id),
                "uploaded_at": now.isoformat(),
            },
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        rows.append(row)

    await db.commit()
    for row in rows:
        await db.refresh(row)

    invalidate_workspace_context_cache(workspace_id)
    await _invalidate_workspace_caches([workspace_id])

    return ChatFileUploadResponse(
        total=len(rows),
        items=[_to_response(row) for row in rows],
    )


@router.get("/{context_id}/download")
async def download_chat_file(
    workspace_id: str,
    context_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    workspace_crud: WorkspaceCRUDDep,
    db: Annotated[AsyncSession, Depends(get_structure_db)],
    storage: Annotated[S3StorageBackend, Depends(get_global_s3_storage)],
):
    """Download the original bytes for a workspace chat-file context row."""
    await _ensure_workspace(workspace_id, current_user, workspace_crud)

    try:
        context_uuid = UUID(context_id)
        workspace_uuid = UUID(workspace_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace or context ID",
        ) from exc

    result = await db.execute(
        select(WorkspaceContext).where(
            and_(
                WorkspaceContext.id == context_uuid,
                WorkspaceContext.workspace_id == workspace_uuid,
                WorkspaceContext.is_deleted.is_(False),
            )
        )
    )
    item = result.scalar_one_or_none()
    if not item or not item.s3_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chat file {context_id} not found",
        )

    try:
        data = storage.get_bytes(item.s3_key)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to download chat file: {exc!s}",
        ) from exc

    filename = str((item.meta or {}).get("original_name") or item.name)
    encoded_filename = quote(filename)
    return Response(
        content=data,
        media_type=item.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Content-Length": str(len(data)),
        },
    )
