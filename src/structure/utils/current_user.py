"""Dependency injection utilities for getting current user information.

DEPRECATED: This module is deprecated. Use structure.dependencies.auth instead.

The correct implementation is in structure.dependencies.auth which provides:
- get_current_user: Returns UserResponse (full user object from DB)
- get_current_active_user: Returns UserResponse (active users only)
- get_admin_user: Returns UserResponse (admin users only)
- get_token_data: Returns TokenData (token claims only, no DB lookup)

Usage:
    from typing import Annotated
    from fastapi import Depends
    from structure.dependencies.auth import get_current_user
    from structure.schemas.auth.user import UserResponse

    @router.get("/profile")
    async def get_profile(
        current_user: Annotated[UserResponse, Depends(get_current_user)]
    ):
        return {"user_id": current_user.id, "username": current_user.username}
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from structure.schemas.auth.auth import TokenData
from structure.schemas.auth.user import UserResponse


async def get_current_user(request: Request) -> TokenData:
    """
    DEPRECATED: Use structure.dependencies.auth.get_current_user instead.

    Legacy dependency to get current user from request state.
    This returns TokenData instead of UserResponse.

    For new code, use:
        from structure.dependencies.auth import get_current_user
        current_user: Annotated[UserResponse, Depends(get_current_user)]
    """
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return request.state.user


async def get_current_active_user(
    current_user: Annotated[TokenData, Depends(get_current_user)],
) -> TokenData:
    """
    DEPRECATED: Use structure.dependencies.auth.get_current_active_user instead.

    For new code, use:
        from structure.dependencies.auth import get_current_active_user
        current_user: Annotated[UserResponse, Depends(get_current_active_user)]
    """
    return current_user


def require_role(required_role: str):
    """
    DEPRECATED: Use structure.dependencies.auth.get_admin_user or custom dependency instead.

    Legacy dependency factory to require a specific role.
    """

    async def role_checker(
        current_user: Annotated[TokenData, Depends(get_current_user)],
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
    DEPRECATED: Use structure.dependencies.auth.get_admin_user or custom dependency instead.

    Legacy dependency factory to require any of the specified roles.
    """

    async def role_checker(
        current_user: Annotated[TokenData, Depends(get_current_user)],
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
