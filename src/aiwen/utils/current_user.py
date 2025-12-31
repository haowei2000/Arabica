"""Dependency injection utilities for getting current user information."""
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

from aiwen.schemas.auth.auth import TokenData


async def get_current_user(request: Request) -> TokenData:
    """
    Dependency to get current user from request state.

    This dependency extracts the user information that was set by the AuthMiddleware
    and makes it available to endpoint handlers.

    Usage:
        @router.get("/profile")
        async def get_profile(current_user: TokenData = Depends(get_current_user)):
            return {"user_id": current_user.user_id, "role": current_user.role}

    Args:
        request: The FastAPI request object containing user data in state

    Returns:
        TokenData: Current user information

    Raises:
        HTTPException: If user is not authenticated
    """
    if not hasattr(request.state, 'user') or not request.state.user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return request.state.user


async def get_current_active_user(
    current_user: TokenData = Depends(get_current_user)
) -> TokenData:
    """
    Dependency to get current active user.

    This dependency ensures the user is not only authenticated but also active.

    Usage:
        @router.get("/profile")
        async def get_profile(current_user: TokenData = Depends(get_current_active_user)):
            return {"user_id": current_user.user_id, "role": current_user.role}

    Args:
        current_user: Current user from get_current_user dependency

    Returns:
        TokenData: Current active user information

    Raises:
        HTTPException: If user is not active
    """
    # In a real implementation, you would check if the user is active in the database
    # For now, we assume if the user exists in the token, they are active
    return current_user


def require_role(required_role: str):
    """
    Dependency factory to require a specific role.

    This creates a dependency that checks if the current user has a specific role.

    Usage:
        @router.get("/admin")
        async def admin_endpoint(current_user: TokenData = Depends(require_role("admin"))):
            return {"message": "Admin access granted"}

    Args:
        required_role: The role required to access the endpoint

    Returns:
        Dependency function that validates the user role
    """
    async def role_checker(
        current_user: TokenData = Depends(get_current_user)
    ) -> TokenData:
        if current_user.role != required_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required role: {required_role}",
            )
        return current_user

    return role_checker


def require_any_role(required_roles: list[str]):
    """
    Dependency factory to require any of the specified roles.

    This creates a dependency that checks if the current user has any of the specified roles.

    Usage:
        @router.get("/moderator")
        async def moderator_endpoint(current_user: TokenData = Depends(require_any_role(["admin", "moderator"]))):
            return {"message": "Moderator access granted"}

    Args:
        required_roles: List of roles that can access the endpoint

    Returns:
        Dependency function that validates the user role
    """
    async def role_checker(
        current_user: TokenData = Depends(get_current_user)
    ) -> TokenData:
        if current_user.role not in required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {required_roles}",
            )
        return current_user

    return role_checker


# Convenience dependencies for common roles
get_current_admin_user = require_role("admin")
get_current_moderator_user = require_any_role(["admin", "moderator"])
