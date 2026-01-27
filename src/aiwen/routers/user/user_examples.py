"""Example routes showing how to use FastAPI's recommended authentication approach."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from aiwen.dependencies.auth import (
    get_current_active_user,
    get_current_user,
    get_token_data,
)
from aiwen.schemas.auth.auth import TokenData
from aiwen.schemas.auth.user import UserResponse

router = APIRouter(prefix="/user-examples", tags=["user-examples"])


@router.get("/profile")
async def get_user_profile(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """
    Get current user profile using dependency injection.

    This endpoint demonstrates how to get the current user information
    using the get_current_user dependency following FastAPI best practices.

    Args:
        current_user: Current user information from database

    Returns:
        User profile information
    """
    return {
        "user_id": str(current_user.id),
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role,
        "tenant_id": str(current_user.tenant_id),
        "is_active": current_user.is_active,
        "input": "Successfully retrieved user profile",
    }


@router.get("/secure-profile")
async def get_secure_profile(
    current_user: Annotated[UserResponse, Depends(get_current_active_user)],
):
    """
    Get current user profile with active user check.

    This endpoint demonstrates how to ensure the user is not only authenticated
    but also active using the get_current_active_user dependency.

    Args:
        current_user: Current active user information

    Returns:
        Secure user profile information
    """
    return {
        "user_id": str(current_user.id),
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role,
        "tenant_id": str(current_user.tenant_id),
        "status": "active",
        "input": "Successfully retrieved secure profile",
    }


@router.get("/admin-panel")
async def admin_panel(current_user: Annotated[UserResponse, Depends(get_current_user)]):
    """
    Admin-only panel.

    This endpoint demonstrates how to restrict access to admin users
    by checking the role after authentication.

    Args:
        current_user: Current user information

    Returns:
        Admin panel data
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
        )

    return {
        "user_id": str(current_user.id),
        "username": current_user.username,
        "role": current_user.role,
        "input": "Welcome to admin panel",
        "admin_data": "sensitive_admin_information",
    }


@router.get("/moderator-area")
async def moderator_area(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """
    Moderator area accessible by admins and moderators.

    This endpoint demonstrates how to restrict access to users with
    specific roles.

    Args:
        current_user: Current user with required role

    Returns:
        Moderator area data
    """
    if current_user.role not in ["admin", "moderator"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Moderator or admin access required",
        )

    return {
        "user_id": str(current_user.id),
        "username": current_user.username,
        "role": current_user.role,
        "input": "Welcome to moderator area",
        "moderator_data": "moderation_tools",
    }


@router.get("/premium-content")
async def premium_content(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """
    Premium content for premium users.

    This endpoint demonstrates how to create role-specific access.

    Args:
        current_user: Current user information

    Returns:
        Premium user data
    """
    if current_user.role != "premium":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Premium access required"
        )

    return {
        "user_id": str(current_user.id),
        "username": current_user.username,
        "role": current_user.role,
        "input": "Premium features unlocked",
        "premium_data": "exclusive_content",
    }


@router.get("/request-and-user")
async def request_and_user_example(
    request: Request, current_user: Annotated[UserResponse, Depends(get_current_user)]
):
    """
    Example showing how to access both request and user information.

    This endpoint demonstrates how to access the raw request object
    alongside the current user information.

    Args:
        request: Raw request object
        current_user: Current user information

    Returns:
        Combined request and user information
    """
    return {
        "user_id": str(current_user.id),
        "username": current_user.username,
        "role": current_user.role,
        "tenant_id": str(current_user.tenant_id),
        "request_method": request.method,
        "request_path": request.url.path,
        "client_host": request.client.host,
        "input": "Successfully accessed request and user info",
    }


@router.get("/token-only")
async def token_only_example(token_data: Annotated[TokenData, Depends(get_token_data)]):
    """
    Example using only token data without database lookup.

    This is more efficient when you only need token claims.

    Args:
        token_data: Decoded token data

    Returns:
        Token information
    """
    return {
        "user_id": str(token_data.user_id),
        "role": token_data.role,
        "tenant_id": str(token_data.tenant_id),
        "input": "Token data retrieved without database lookup",
    }
