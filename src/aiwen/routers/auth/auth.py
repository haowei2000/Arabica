"""Authentication routes for user login, registration, and token management."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.dependencies.auth import get_current_user
from aiwen.extensions.database import get_aiwen_db
from aiwen.schemas.auth.auth import Token, TokenRefresh
from aiwen.schemas.auth.user import UserCreate, UserResponse
from aiwen.services.auth.auth_service import AuthService
from aiwen.services.auth.token_service import TokenService

router = APIRouter(prefix="/auth", tags=["authentication"])


async def get_auth_service(
    db: Annotated[AsyncSession, Depends(get_aiwen_db)]
) -> AuthService:
    """Dependency to get AuthService instance."""
    return AuthService(db)


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register_user(
    user_data: UserCreate,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
):
    """
    Register a new user.

    Args:
        user_data: User registration data
        auth_service: Authentication service dependency

    Returns:
        Created user information
    """
    # Check if user already exists
    existing_user = await auth_service.get_user_by_username(user_data.username)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered",
        )

    if user_data.email:
        existing_email = await auth_service.get_user_by_email(user_data.email)
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )

    # For demo purposes, assign user to default tenant
    # In a real application, you would need to determine the tenant
    default_tenant = await auth_service.get_tenant_by_name("default")
    if not default_tenant:
        default_tenant = await auth_service.create_tenant("default", "Default tenant")

    # Create user
    user = await auth_service.create_user(user_data, default_tenant.id)

    return user


@router.post("/login", response_model=Token)
async def login_user(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
):
    """
    Login a user and return JWT tokens.

    Args:
        form_data: OAuth2 password request form containing username and password
        auth_service: Authentication service dependency

    Returns:
        Access and refresh tokens
    """
    # Authenticate user
    user = await auth_service.authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create tokens
    access_token, refresh_token = TokenService.create_tokens(
        user_id=user.id, role=user.role, tenant_id=user.tenant_id
    )

    return Token(
        access_token=access_token, refresh_token=refresh_token, token_type="bearer"
    )


@router.post("/refresh", response_model=Token)
async def refresh_token(token_data: TokenRefresh):
    """
    Refresh access token using refresh token.

    Args:
        token_data: Refresh token data

    Returns:
        New access and refresh tokens
    """
    tokens = TokenService.refresh_tokens(token_data.refresh_token)
    if not tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return Token(access_token=tokens[0], refresh_token=tokens[1], token_type="bearer")


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: Annotated[UserResponse, Depends(get_current_user)]):
    """
    Get current authenticated user information.

    This endpoint uses the standard get_current_user dependency.

    Args:
        current_user: Current authenticated user from dependency

    Returns:
        Current user information
    """
    return current_user


# @router.post("/register_tenant")
# async def register_tenant(
#         tenant_name: str,
#         auth_service: AuthService = Depends(get_auth_service),
#         current_user: UserResponse = Depends(get_current_user)
# ):
#     """
#     Register a new tenant for the current user.
#
#     Args:
#         tenant_name: Name of the tenant to register
#         auth_service: Authentication service dependency
#         current_user: Current authenticated user
#
#     Returns:
#         Created tenant information
#     """
#     return await auth_service.create_tenant(tenant_name, current_user.id)
