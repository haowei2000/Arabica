"""Test routes for authentication system using FastAPI's recommended approach."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from aiwen.dependencies.auth import get_current_user, get_token_data
from aiwen.schemas.auth.auth import TokenData
from aiwen.schemas.auth.user import UserResponse

router = APIRouter(prefix="/test-auth", tags=["test-auth"])


@router.get("/public")
async def public_endpoint():
    """Public endpoint that doesn't require authentication."""
    return {"input": "This is a public endpoint"}


@router.get("/protected")
async def protected_endpoint(
    current_user: Annotated[UserResponse, Depends(get_current_user)]
):
    """Protected endpoint that requires authentication using FastAPI dependencies."""
    return {
        "input": "This is a protected endpoint",
        "user": {
            "id": str(current_user.id),
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "tenant_id": str(current_user.tenant_id)
        }
    }


@router.get("/admin-only")
async def admin_only_endpoint(
    current_user: Annotated[UserResponse, Depends(get_current_user)]
):
    """Admin-only endpoint using FastAPI dependencies."""
    # Check if user has admin role
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )

    return {
        "input": "This is an admin-only endpoint",
        "user": {
            "id": str(current_user.id),
            "username": current_user.username,
            "role": current_user.role,
            "tenant_id": str(current_user.tenant_id)
        }
    }


@router.get("/token-info")
async def token_info_endpoint(
    token_data: Annotated[TokenData, Depends(get_token_data)]
):
    """
    Endpoint showing token data without full user lookup.

    This is more efficient when you only need token claims.
    """
    return {
        "input": "Token information retrieved",
        "token_data": {
            "user_id": str(token_data.user_id),
            "role": token_data.role,
            "tenant_id": str(token_data.tenant_id)
        }
    }
