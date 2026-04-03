"""REST API endpoints for User management."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.dependencies.auth import get_admin_user, get_current_user
from structure.extensions.database import get_structure_db
from structure.schemas.auth.user import UserCreate, UserResponse
from structure.services.auth.auth_service import AuthService
from structure.services.auth.user_crud import UserCRUD

router = APIRouter(prefix="/users", tags=["users"])


async def get_user_crud(db: Annotated[AsyncSession, Depends(get_structure_db)]):
    """Dependency to get UserCRUD instance."""
    return UserCRUD(db)


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    user_crud: Annotated[UserCRUD, Depends(get_user_crud)],
    current_user: Annotated[UserResponse, Depends(get_admin_user)],
):
    """
    Create a new user.

    Args:
        user_data: User creation data
        user_crud: User CRUD dependency
        current_user: Current authenticated admin user

    Returns:
        Created user information

    Raises:
        HTTPException 400: If username or email already exists
        HTTPException 403: If current user is not admin
    """
    # Check if user already exists
    existing_user = await user_crud.get_user_by_username(user_data.username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered",
        )

    if user_data.email:
        existing_email = await user_crud.get_user_by_email(user_data.email)
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )

    # For demo purposes, assign user to default tenant
    # In a real application, you would need to determine the tenant
    auth_service = AuthService(user_crud.db_session)
    default_tenant = await auth_service.get_tenant_by_name("default")
    if not default_tenant:
        default_tenant = await auth_service.create_tenant("default", current_user.id)

    # Create user
    user = await user_crud.create_user(user_data, default_tenant.id)

    return user  # noqa: RET504


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    user_crud: Annotated[UserCRUD, Depends(get_user_crud)],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """
    Get user by ID.

    Args:
        user_id: The user ID to retrieve
        user_crud: User CRUD dependency
        current_user: Current authenticated user

    Returns:
        User information

    Raises:
        HTTPException 404: If user not found
        HTTPException 403: If user doesn't have permission to access this user
    """
    user = await user_crud.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{user_id}' not found",
        )

    # Check if current user has permission to access this user
    # Regular users can only access their own data, admins can access any user
    if user.id != current_user.id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to access this user",
        )

    return user


@router.get("/", response_model=list[UserResponse])
async def list_users(
    user_crud: Annotated[UserCRUD, Depends(get_user_crud)],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(
        100, ge=1, le=1000, description="Maximum number of records to return"
    ),
    tenant_id: UUID | None = Query(None, description="Filter by tenant ID"),  # noqa: B008
    is_active: bool | None = Query(None, description="Filter by active status"),
):
    """
    List all users with pagination.

    Args:
        user_crud: User CRUD dependency
        current_user: Current authenticated user
        skip: Number of records to skip
        limit: Maximum number of records to return
        tenant_id: Filter by tenant ID
        is_active: Filter by active status

    Returns:
        List of users

    Raises:
        HTTPException 403: If current user is not admin
    """
    # Only admin users can list all users
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin users can list all users",
        )

    users, total = await user_crud.list_users(  # noqa: RUF059
        skip=skip, limit=limit, tenant_id=tenant_id, is_active=is_active
    )

    return users


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    user_crud: Annotated[UserCRUD, Depends(get_user_crud)],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    username: str | None = Body(None),
    email: str | None = Body(None),
    phone: str | None = Body(None),
    role: str | None = Body(None),
    is_active: bool | None = Body(None),
    is_superuser: bool | None = Body(None),
):
    """
    Update an existing user.

    Args:
        user_id: The user ID to update
        username: New username (optional)
        email: New email (optional)
        phone: New phone (optional)
        role: New role (optional)
        is_active: New active status (optional)
        is_superuser: New superuser status (optional)
        user_crud: User CRUD dependency
        current_user: Current authenticated user

    Returns:
        Updated user information

    Raises:
        HTTPException 404: If user not found
        HTTPException 403: If current user doesn't have permission to update this user
    """
    # Only admin users can update other users
    if not current_user.is_superuser and current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to update this user",
        )

    user = await user_crud.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{user_id}' not found",
        )

    updated_user = await user_crud.update_user(
        user_id=user_id,
        username=username,
        email=email,
        phone=phone,
        role=role,
        is_active=is_active,
        is_superuser=is_superuser,
    )

    return updated_user  # noqa: RET504


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    user_crud: Annotated[UserCRUD, Depends(get_user_crud)],
    current_user: Annotated[UserResponse, Depends(get_admin_user)],
):
    """
    Delete a user.

    Args:
        user_id: The user ID to delete
        user_crud: User CRUD dependency
        current_user: Current authenticated admin user

    Returns:
        None

    Raises:
        HTTPException 404: If user not found
        HTTPException 403: If current user doesn't have permission to delete this user
    """
    # Only admin users can delete users
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to delete users",
        )

    user = await user_crud.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{user_id}' not found",
        )

    await user_crud.delete_user(user_id)
    return
