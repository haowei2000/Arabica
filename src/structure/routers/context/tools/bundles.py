"""Tool bundle management API."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.dependencies.auth import get_current_user
from structure.extensions.database import get_aiwen_db
from structure.schemas.auth.user import UserResponse
from structure.schemas.context.tools.tool_bundle import (
    ToolBundleCreate,
    ToolBundleListResponse,
    ToolBundleResponse,
    ToolBundleUpdate,
)
from structure.services.context.tools.tool_bundle_crud import ToolBundleCRUD

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tool-bundles", tags=["tool-bundles"])


def _build_bundle_response(bundle) -> ToolBundleResponse:
    tool_ids = [item.tool_id for item in (bundle.items or [])]
    return ToolBundleResponse(
        id=bundle.id,
        bundle_type=bundle.bundle_type,
        source=bundle.source,
        user_id=bundle.user_id,
        name=bundle.name,
        description=bundle.description,
        tags=bundle.tags,
        is_public=bundle.is_public,
        tool_ids=tool_ids,
        tool_count=len(tool_ids),
        created_at=bundle.created_at,
        updated_at=bundle.updated_at,
    )


@router.post(
    "",
    response_model=ToolBundleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tool bundle",
)
async def create_tool_bundle(
    data: ToolBundleCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    crud = ToolBundleCRUD(db)
    try:
        bundle = await crud.create(current_user.id, data)
        return _build_bundle_response(bundle)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get(
    "",
    response_model=ToolBundleListResponse,
    summary="List tool bundles",
)
async def list_tool_bundles(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
    include_public: bool = True,
    tags: str | None = None,
):
    crud = ToolBundleCRUD(db)
    tag_list = [tag.strip() for tag in tags.split(",")] if tags else None
    bundles = await crud.list(
        user_id=current_user.id,
        include_public=include_public,
        tags=tag_list,
    )
    responses = [_build_bundle_response(b) for b in bundles]
    return ToolBundleListResponse(bundles=responses, total=len(responses))


@router.get(
    "/{bundle_id}",
    response_model=ToolBundleResponse,
    summary="Get tool bundle by ID",
)
async def get_tool_bundle(
    bundle_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    crud = ToolBundleCRUD(db)
    bundle = await crud.get_by_id(bundle_id, current_user.id)
    if not bundle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bundle not found")
    return _build_bundle_response(bundle)


@router.patch(
    "/{bundle_id}",
    response_model=ToolBundleResponse,
    summary="Update a tool bundle",
)
async def update_tool_bundle(
    bundle_id: UUID,
    data: ToolBundleUpdate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    crud = ToolBundleCRUD(db)
    try:
        bundle = await crud.update(bundle_id, current_user.id, data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if not bundle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bundle not found or you don't have permission",
        )
    return _build_bundle_response(bundle)


@router.delete(
    "/{bundle_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tool bundle",
)
async def delete_tool_bundle(
    bundle_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    crud = ToolBundleCRUD(db)
    success = await crud.delete(bundle_id, current_user.id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bundle not found or you don't have permission",
        )
