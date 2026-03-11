# aiwen/routers/workspaces/workspace.py
"""REST API endpoints for workspace management."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import (
    and_,
    func,
    select,
    update as sql_update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.core.dependencies.auth import get_current_user
from aiwen.core.dependencies.workspace import (
    WorkspaceCRUDDep,
    WorkspaceMemberCRUDDep,
)
from aiwen.extensions.database import get_aiwen_db
from aiwen.models.context.workspace_context import WorkspaceContext
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.workspaces.workspace import (
    MemberRole,
    WorkspaceContextConfig,
    WorkspaceCreate,
    WorkspaceListResponse,
    WorkspaceMemberCreate,
    WorkspaceMemberResponse,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from aiwen.schemas.workspaces.workspace_context import (
    CopyContextsRequest,
    CopyContextsResponse,
    WorkspaceContextListResponse,
)
from aiwen.schemas.auth.friend import FriendUserInfo
from aiwen.services.auth.friend_crud import FriendCRUD
from aiwen.services.context.process import copy_contexts_to_workspace

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    data: WorkspaceCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: WorkspaceCRUDDep,
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Create a new workspace.

    Args:
        data: Workspace creation data
        current_user: Current authenticated user
        crud: Workspace CRUD service
        db: Database session

    Returns:
        Created workspace
    """
    # Validate app_id exists if provided
    if data.app_id:
        from aiwen.models.app.app import App

        app_stmt = select(App).where(App.id == UUID(data.app_id))
        app_result = await db.execute(app_stmt)
        if not app_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"App {data.app_id} not found",
            )

    workspace = await crud.create(
        owner_id=current_user.id,
        name=data.name,
        description=data.description,
        app_id=data.app_id,
        executor_code=data.executor_code,
        executor_config=data.executor_config,
        visibility=data.visibility.value if data.visibility else "private",
        settings=data.settings,
        auto_commit=True,
    )

    if data.context_config and any([
        data.context_config.tool_ids,
        data.context_config.knowledge_ids,
        data.context_config.skill_ids,
        data.context_config.source_workspace_ids,
        data.context_config.memory_ids,
        data.context_config.trigger_ids,
    ]):
        from aiwen.services.workspace_context.init_workspace_context import (
            init_workspace_context,
        )
        await init_workspace_context(
            db=db,
            workspace_id=workspace.id,
            user_id=current_user.id,
            config=data.context_config,
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
        executor_code=data.executor_code,
        executor_config=data.executor_config,
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
    status_filter: str | None = Query(
        None, alias="status", description="Filter by status"
    ),
    app_id: str | None = Query(None, description="Filter by app ID"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """
    List workspaces accessible to current user.

    Args:
        current_user: Current authenticated user
        crud: Workspace CRUD service
        status_filter: Filter by status
        app_id: Filter by app ID
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
        app_id=app_id,
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


@router.patch(
    "/{workspace_id}/members/{user_id}", response_model=WorkspaceMemberResponse
)
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


@router.delete(
    "/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
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


# Workspace context endpoints


@router.get(
    "/{workspace_id}/contexts",
    response_model=WorkspaceContextListResponse,
)
async def list_workspace_contexts(
    workspace_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: WorkspaceCRUDDep,
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=500, description="Items per page"),
):
    """List workspace contexts (paginated)."""
    workspace = await crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    ws_id = UUID(workspace_id)
    base_filter = and_(
        WorkspaceContext.workspace_id == ws_id,
        WorkspaceContext.is_deleted == False,  # noqa: E712
    )

    count_stmt = select(func.count()).select_from(WorkspaceContext).where(base_filter)
    total = (await db.execute(count_stmt)).scalar_one()

    skip = (page - 1) * page_size
    items_stmt = (
        select(WorkspaceContext)
        .where(base_filter)
        .order_by(WorkspaceContext.created_at.desc())
        .offset(skip)
        .limit(page_size)
    )
    items = list((await db.execute(items_stmt)).scalars().all())

    return WorkspaceContextListResponse(
        total=total,
        items=items,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/{workspace_id}/contexts/copy",
    response_model=CopyContextsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def copy_contexts_to_workspace_endpoint(
    workspace_id: str,
    body: CopyContextsRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: WorkspaceCRUDDep,
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """Copy selected user contexts into the workspace."""
    workspace = await crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    context_uuids = [UUID(cid) for cid in body.context_ids]
    created = await copy_contexts_to_workspace(
        db,
        workspace_id=UUID(workspace_id),
        context_ids=context_uuids,
        created_by=UUID(current_user.id),
        path_prefix=body.path_prefix,
        auto_commit=True,
    )

    return CopyContextsResponse(
        copied_count=len(created),
        items=created,
    )


@router.delete(
    "/{workspace_id}/contexts/{context_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_workspace_context(
    workspace_id: str,
    context_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: WorkspaceCRUDDep,
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """Remove a context entry from the workspace (soft delete)."""
    workspace = await crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    stmt = select(WorkspaceContext).where(
        and_(
            WorkspaceContext.id == UUID(context_id),
            WorkspaceContext.workspace_id == UUID(workspace_id),
            WorkspaceContext.is_deleted == False,  # noqa: E712
        )
    )
    result = await db.execute(stmt)
    ws_ctx = result.scalar_one_or_none()
    if not ws_ctx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace context {context_id} not found",
        )

    ws_ctx.is_deleted = True
    await db.commit()


@router.post("/{workspace_id}/context/reinit", status_code=status.HTTP_200_OK)
async def reinit_workspace_context(
    workspace_id: str,
    config: WorkspaceContextConfig,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: WorkspaceCRUDDep,
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
) -> dict:
    """
    Re-initialize workspace context.

    Soft-deletes all non-history context entries for the workspace, then
    repopulates from the supplied config (tools, knowledge, skills, memories).
    History entries are preserved.
    """
    workspace = await crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    # Soft-delete all non-history context entries
    history_prefix = "/long_memory/%"
    await db.execute(
        sql_update(WorkspaceContext)
        .where(
            and_(
                WorkspaceContext.workspace_id == UUID(workspace_id),
                WorkspaceContext.is_deleted == False,  # noqa: E712
                ~WorkspaceContext.path.like(history_prefix),
            )
        )
        .values(is_deleted=True)
    )
    await db.commit()

    # Re-populate with new config
    from aiwen.services.workspace_context.init_workspace_context import (
        init_workspace_context,
    )

    counts = await init_workspace_context(
        db=db,
        workspace_id=workspace_id,
        user_id=current_user.id,
        config=config,
    )
    return {"success": True, "counts": counts}


@router.get(
    "/{workspace_id}/invitable-friends",
    response_model=list[FriendUserInfo],
    summary="List friends who can be invited to this workspace",
)
async def list_invitable_friends(
    workspace_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: WorkspaceCRUDDep,
    member_crud: WorkspaceMemberCRUDDep,
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """List the current user's friends who are not yet members of this workspace.

    This is a convenience endpoint to make workspace invitation easier.
    """
    workspace = await crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    friend_crud = FriendCRUD(db)
    _, all_friends = await friend_crud.list_friends(user_id=current_user.id, page=1, page_size=1000)

    # Get existing member user IDs
    existing_members, _ = await member_crud.list_members(workspace_id=workspace_id, skip=0, limit=10000)
    existing_member_ids = {str(m.user_id) for m in existing_members}

    invitable = [
        FriendUserInfo(**f)
        for f in all_friends
        if str(f["id"]) not in existing_member_ids
    ]
    return invitable
