"""REST API endpoints for friend management."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.dependencies.auth import get_current_user
from structure.extensions.database import get_structure_db
from structure.schemas.auth.friend import (
    FriendListResponse,
    FriendRequestCreate,
    FriendResponse,
    FriendUserInfo,
)
from structure.schemas.auth.user import UserResponse
from structure.services.auth.friend_crud import FriendCRUD

router = APIRouter(prefix="/friends", tags=["friends"])


def _get_friend_crud(
    db: Annotated[AsyncSession, Depends(get_structure_db)],
) -> FriendCRUD:
    return FriendCRUD(db)


FriendCRUDDep = Annotated[FriendCRUD, Depends(_get_friend_crud)]


@router.post(
    "/request",
    response_model=FriendResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Send a friend request",
)
async def send_friend_request(
    data: FriendRequestCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: FriendCRUDDep,
    db: Annotated[AsyncSession, Depends(get_structure_db)],  # noqa: ARG001
):
    """Send a friend request to another user by their user ID."""
    try:
        record = await crud.send_request(
            user_id=current_user.id,
            friend_id=data.friend_id,
            auto_commit=True,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))  # noqa: B904

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A friend request already exists between these users",
        )
    return record


@router.get(
    "",
    response_model=FriendListResponse,
    summary="List accepted friends",
)
async def list_friends(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: FriendCRUDDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List all accepted friends of the current user."""
    total, items = await crud.list_friends(
        user_id=current_user.id,
        page=page,
        page_size=page_size,
    )
    return FriendListResponse(
        total=total,
        items=[FriendUserInfo(**item) for item in items],
        page=page,
        page_size=page_size,
    )


@router.get(
    "/requests",
    response_model=FriendListResponse,
    summary="List incoming pending friend requests",
)
async def list_friend_requests(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: FriendCRUDDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """List all pending incoming friend requests for the current user."""
    total, items = await crud.list_pending_requests(
        user_id=current_user.id,
        page=page,
        page_size=page_size,
    )
    return FriendListResponse(
        total=total,
        items=[FriendUserInfo(**item) for item in items],
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/{friend_record_id}/accept",
    response_model=FriendResponse,
    summary="Accept a friend request",
)
async def accept_friend_request(
    friend_record_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: FriendCRUDDep,
):
    """Accept a pending friend request. Only the recipient can accept."""
    try:
        record = await crud.accept_request(
            friend_record_id=friend_record_id,
            current_user_id=current_user.id,
            auto_commit=True,
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))  # noqa: B904
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))  # noqa: B904

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Friend request not found"
        )
    return record


@router.patch(
    "/{friend_record_id}/decline",
    response_model=FriendResponse,
    summary="Decline a friend request",
)
async def decline_friend_request(
    friend_record_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: FriendCRUDDep,
):
    """Decline a pending friend request. Only the recipient can decline."""
    try:
        record = await crud.decline_request(
            friend_record_id=friend_record_id,
            current_user_id=current_user.id,
            auto_commit=True,
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))  # noqa: B904
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))  # noqa: B904

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Friend request not found"
        )
    return record


@router.delete(
    "/{friend_record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a friend",
)
async def remove_friend(
    friend_record_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: FriendCRUDDep,
):
    """Remove an accepted friendship. Either party can remove."""
    try:
        removed = await crud.remove_friend(
            friend_record_id=friend_record_id,
            current_user_id=current_user.id,
            auto_commit=True,
        )
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))  # noqa: B904

    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Friend record not found"
        )
