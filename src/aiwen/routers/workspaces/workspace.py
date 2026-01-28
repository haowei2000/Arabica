# aiwen/routers/workspaces/workspace.py
"""REST API endpoints for workspace management."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from aiwen.dependencies.auth import get_current_user
from aiwen.dependencies.workspace import (
    WorkspaceCRUDDep,
    WorkspaceMemberCRUDDep,
)
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.workspace.workspace import (
    MemberRole,
    WorkspaceCreate,
    WorkspaceListResponse,
    WorkspaceMemberCreate,
    WorkspaceMemberResponse,
    WorkspaceResponse,
    WorkspaceUpdate,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post(
    "", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED
)
async def create_workspace(
        data: WorkspaceCreate,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        crud: WorkspaceCRUDDep,
):
    """
    Create a new workspace.

    Args:
        data: Workspace creation data
        current_user: Current authenticated user
        crud: Workspace CRUD service

    Returns:
        Created workspace
    """
    workspace = await crud.create(
        owner_id=current_user.id,
        name=data.name,
        description=data.description,
        app_id=data.app_id,
        visibility=data.visibility.value if data.visibility else "private",
        settings=data.settings,
        auto_commit=True,
    )
    return workspace


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
        workspace_id: str,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        crud: WorkspaceCRUDDep,
):
    """
    Get workspace by ID.

    Args:
        workspace_id: The workspace ID
        current_user: Current authenticated user
        crud: Workspace CRUD service

    Returns:
        Workspace details

    Raises:
        HTTPException: If workspace not found or not authorized
    """
    workspace = await crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )
    return workspace


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
        workspace_id: str,
        data: WorkspaceUpdate,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        crud: WorkspaceCRUDDep,
):
    """
    Update a workspace.

    Args:
        workspace_id: The workspace ID
        data: Update data
        current_user: Current authenticated user
        crud: Workspace CRUD service

    Returns:
        Updated workspace

    Raises:
        HTTPException: If workspace not found or not authorized
    """
    workspace = await crud.update(
        workspace_id=workspace_id,
        user_id=current_user.id,
        name=data.name,
        description=data.description,
        app_id=data.app_id,
        visibility=data.visibility.value if data.visibility else None,
        is_shared=data.is_shared,
        settings=data.settings,
        status=data.status.value if data.status else None,
        auto_commit=True,
    )
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )
    return workspace


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
        workspace_id: str,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        crud: WorkspaceCRUDDep,
):
    """
    Delete a workspace (soft delete).

    Args:
        workspace_id: The workspace ID
        current_user: Current authenticated user
        crud: Workspace CRUD service

    Raises:
        HTTPException: If workspace not found or not owner
    """
    deleted = await crud.delete(
        workspace_id=workspace_id,
        user_id=current_user.id,
        auto_commit=True,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or you are not the owner",
        )


@router.get("", response_model=WorkspaceListResponse)
async def list_workspaces(
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        crud: WorkspaceCRUDDep,
        status_filter: str | None = Query(None, alias="status", description="Filter by status"),
        page: int = Query(1, ge=1, description="Page number"),
        page_size: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """
    List workspaces accessible to current user.

    Args:
        current_user: Current authenticated user
        crud: Workspace CRUD service
        status_filter: Filter by status
        page: Page number
        page_size: Items per page

    Returns:
        Paginated list of workspaces
    """
    skip = (page - 1) * page_size
    items, total = await crud.list_by_user(
        user_id=current_user.id,
        skip=skip,
        limit=page_size,
        status=status_filter,
    )
    return WorkspaceListResponse(
        total=total,
        items=items,
        page=page,
        page_size=page_size,
    )


# Member management endpoints


@router.post(
    "/{workspace_id}/members",
    response_model=WorkspaceMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
        workspace_id: str,
        data: WorkspaceMemberCreate,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        workspace_crud: WorkspaceCRUDDep,
        member_crud: WorkspaceMemberCRUDDep,
):
    """
    Add a member to workspace.

    Args:
        workspace_id: The workspace ID
        data: Member data
        current_user: Current authenticated user
        workspace_crud: Workspace CRUD service
        member_crud: Member CRUD service

    Returns:
        Created membership

    Raises:
        HTTPException: If workspace not found, not authorized, or member exists
    """
    # Check workspace access and permission
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    # Check if current user can add members (owner or admin)
    user_role = await member_crud.get_user_role(workspace_id, current_user.id)
    if user_role not in ["owner", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owners and admins can add members",
        )

    member = await member_crud.add_member(
        workspace_id=workspace_id,
        user_id=data.user_id,
        role=data.role.value if data.role else "viewer",
        invited_by=current_user.id,
        invitation_status="pending",
        auto_commit=True,
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this workspace",
        )
    return member


@router.get("/{workspace_id}/members", response_model=list[WorkspaceMemberResponse])
async def list_members(
        workspace_id: str,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        workspace_crud: WorkspaceCRUDDep,
        member_crud: WorkspaceMemberCRUDDep,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=100),
):
    """
    List workspace members.

    Args:
        workspace_id: The workspace ID
        current_user: Current authenticated user
        workspace_crud: Workspace CRUD
        member_crud: Member CRUD

    Returns:
        List of members
    """
    # Check workspace access
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    skip = (page - 1) * page_size
    members, _ = await member_crud.list_members(
        workspace_id=workspace_id,
        skip=skip,
        limit=page_size,
        status="accepted",
    )
    return members


@router.patch("/{workspace_id}/members/{user_id}", response_model=WorkspaceMemberResponse)
async def update_member_role(
        workspace_id: str,
        user_id: str,
        role: MemberRole,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        workspace_crud: WorkspaceCRUDDep,
        member_crud: WorkspaceMemberCRUDDep,
):
    """
    Update a member's role.

    Args:
        workspace_id: The workspace ID
        user_id: The member's user ID
        role: New role
        current_user: Current authenticated user

    Returns:
        Updated membership
    """
    # Check workspace access
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    # Check permission
    current_role = await member_crud.get_user_role(workspace_id, current_user.id)
    if current_role not in ["owner", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owners and admins can update member roles",
        )

    member = await member_crud.update_role(
        workspace_id=workspace_id,
        user_id=user_id,
        new_role=role.value,
        auto_commit=True,
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found or cannot change owner role",
        )
    return member


@router.delete("/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
        workspace_id: str,
        user_id: str,
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        workspace_crud: WorkspaceCRUDDep,
        member_crud: WorkspaceMemberCRUDDep,
):
    """
    Remove a member from workspace.

    Args:
        workspace_id: The workspace ID
        user_id: The member's user ID
        current_user: Current authenticated user
    """
    # Check workspace access
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    # Check permission (owner/admin can remove, or user can remove themselves)
    current_role = await member_crud.get_user_role(workspace_id, current_user.id)
    is_self = str(current_user.id) == user_id
    if current_role not in ["owner", "admin"] and not is_self:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owners and admins can remove members",
        )

    removed = await member_crud.remove_member(
        workspace_id=workspace_id,
        user_id=user_id,
        auto_commit=True,
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found or cannot remove owner",
        )
