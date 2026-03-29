"""
Authentication dependencies using FastAPI's recommended approach.

This module provides reusable dependencies for authentication and authorization
following FastAPI best practices with OAuth2PasswordBearer.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from structure.extensions.database import get_aiwen_db
from structure.schemas.auth.auth import TokenData
from structure.schemas.auth.user import UserResponse
from structure.services.auth.auth_service import AuthService
from structure.services.auth.token_service import TokenService

# OAuth2 scheme - this will extract the token from Authorization header
# tokenUrl points to the login endpoint
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
) -> UserResponse:
    """
    Get the current authenticated user from the JWT token.

    This is the main authentication dependency. Use this in any route
    that requires user authentication.

    Args:
        token: JWT access token extracted from Authorization header
        db: Database session

    Returns:
        UserResponse: Current authenticated user

    Raises:
        HTTPException: 401 if token is invalid or user not found

    Example:
        @router.get("/protected")
        async def protected_route(
            current_user: Annotated[UserResponse, Depends(get_current_user)]
        ):
            return {"user": current_user.username}
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Verify and decode the token
    user_data = TokenService.verify_access_token(token)
    if not user_data:
        raise credentials_exception

    # Get user from database
    auth_service = AuthService(db)
    user = await auth_service.get_user_by_id(user_data["user_id"])

    if user is None:
        raise credentials_exception

    return user


async def get_current_active_user(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> UserResponse:
    """
    Get the current authenticated and active user.

    This dependency checks that the user is not only authenticated but also active.
    Use this when you want to ensure the user account is not disabled.

    Args:
        current_user: Current user from get_current_user dependency

    Returns:
        UserResponse: Current active user

    Raises:
        HTTPException: 400 if user is inactive

    Example:
        @router.get("/admin")
        async def admin_route(
            current_user: Annotated[UserResponse, Depends(get_current_active_user)]
        ):
            return {"input": "Admin access granted"}
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user"
        )
    return current_user


def get_token_data(token: Annotated[str, Depends(oauth2_scheme)]) -> TokenData:
    """
    Extract token data without database lookup.

    Use this when you only need the token claims (user_id, role, tenant_id)
    without fetching the full user object from the database.
    This is more efficient for operations that don't need full user details.

    Args:
        token: JWT access token

    Returns:
        TokenData: Decoded token data

    Raises:
        HTTPException: 401 if token is invalid

    Example:
        @router.get("/quick-check")
        async def quick_check(
            token_data: Annotated[TokenData, Depends(get_token_data)]
        ):
            return {"user_id": token_data.user_id}
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    user_data = TokenService.verify_access_token(token)
    if not user_data:
        raise credentials_exception

    return TokenData(
        user_id=user_data["user_id"],
        role=user_data["role"],
        tenant_id=user_data["tenant_id"],
    )


async def get_admin_user(
    current_user: Annotated[UserResponse, Depends(get_current_active_user)],
) -> UserResponse:
    """
    Get the current authenticated user with admin privileges.

    This dependency checks that the current user is both active and has admin
    (superuser) privileges. Use this in routes that require admin access.

    Args:
        current_user: Current active user from get_current_active_user dependency

    Returns:
        UserResponse: Current admin user

    Raises:
        HTTPException: 403 if user is not an admin

    Example:
        @router.delete("/users/{user_id}")
        async def delete_user(
            admin_user: Annotated[UserResponse, Depends(get_admin_user)],
            user_id: UUID
        ):
            return {"input": "User deleted by admin"}
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )
    return current_user
