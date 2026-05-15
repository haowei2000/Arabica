"""Authentication routes for user login, registration, and token management."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from structure.config.factory import get_settings
from structure.core.dependencies.auth import get_current_user
from structure.extensions.database import get_structure_db
from structure.schemas.auth.auth import Token, TokenRefresh
from structure.schemas.auth.user import (
    EmailRegisterRequest,
    EmailVerificationResendRequest,
    EmailVerificationResponse,
    UserCreate,
    UserResponse,
)
from structure.services.auth.auth_service import AuthService
from structure.services.auth.email_service import EmailDeliveryError, EmailService
from structure.services.auth.token_service import TokenService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["authentication"])


async def get_auth_service(
    db: Annotated[AsyncSession, Depends(get_structure_db)],
) -> AuthService:
    """Dependency to get AuthService instance."""
    return AuthService(db)


async def get_email_service() -> EmailService:
    """Dependency to get EmailService instance."""
    return EmailService()


async def _send_verification_email_if_needed(
    user,
    email_service: EmailService,
    require_delivery: bool = False,
) -> bool:
    """Send a verification email when the user has an email address."""
    if not user.email:
        return False

    try:
        delivered = await email_service.send_verification_email(
            user_id=user.id,
            email=user.email,
            username=user.username,
        )
    except EmailDeliveryError as exc:
        logger.exception("Failed to send verification email for user_id=%s", user.id)
        if require_delivery:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Verification email could not be sent. Please try again later.",
            ) from exc
        return False

    if require_delivery and not delivered:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Verification email could not be sent. Please try again later.",
        )

    return delivered


async def _commit_user_with_required_verification(
    user,
    auth_service: AuthService,
    email_service: EmailService,
) -> None:
    """Commit a user only after required verification delivery succeeds."""
    try:
        await _send_verification_email_if_needed(
            user,
            email_service,
            require_delivery=True,
        )
        await auth_service.db.commit()
    except Exception:
        await auth_service.db.rollback()
        raise

    await auth_service.db.refresh(user)


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register_user(
    user_data: UserCreate,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    email_service: Annotated[EmailService, Depends(get_email_service)],
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
        default_tenant = await auth_service.create_tenant(
            "default", description="Default tenant"
        )

    requires_verification_delivery = bool(
        user_data.email and get_settings().auth.require_email_verification
    )

    user = await auth_service.create_user(
        user_data,
        default_tenant.id,
        auto_commit=not requires_verification_delivery,
    )
    if requires_verification_delivery:
        await _commit_user_with_required_verification(
            user,
            auth_service,
            email_service,
        )
    else:
        await _send_verification_email_if_needed(user, email_service)

    return user


@router.post(
    "/register/email",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_user_by_email(
    register_data: EmailRegisterRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    email_service: Annotated[EmailService, Depends(get_email_service)],
):
    """
    Register a new user using email and password.

    The username is auto-generated from the email address local part.

    Args:
        register_data: Email and password
        auth_service: Authentication service dependency

    Returns:
        Created user information
    """
    existing_user = await auth_service.get_user_by_email(register_data.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    default_tenant = await auth_service.get_tenant_by_name("default")
    if not default_tenant:
        default_tenant = await auth_service.create_tenant(
            "default", description="Default tenant"
        )

    requires_verification_delivery = get_settings().auth.require_email_verification

    user = await auth_service.create_user_by_email(
        register_data.email,
        register_data.password,
        default_tenant.id,
        auto_commit=not requires_verification_delivery,
    )
    if requires_verification_delivery:
        await _commit_user_with_required_verification(
            user,
            auth_service,
            email_service,
        )
    else:
        await _send_verification_email_if_needed(user, email_service)
    return user


@router.get("/verify-email", response_model=EmailVerificationResponse)
async def verify_email(
    token: str,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
):
    """Verify a user's email address from a signed email token."""
    payload = TokenService.verify_email_verification_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification link",
        )

    try:
        user_id = UUID(payload["user_id"])
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification link",
        ) from exc

    user = await auth_service.mark_email_verified(user_id, payload["email"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification link",
        )

    return EmailVerificationResponse(
        verified=True,
        message="Email verified successfully",
    )


@router.post("/verify-email/resend", response_model=EmailVerificationResponse)
async def resend_verification_email(
    request: EmailVerificationResendRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    email_service: Annotated[EmailService, Depends(get_email_service)],
):
    """Resend a verification email without disclosing account existence."""
    user = await auth_service.get_user_by_email(str(request.email))
    if user and user.email and not user.email_verified:
        await _send_verification_email_if_needed(user, email_service)

    return EmailVerificationResponse(
        verified=False,
        message="If the account exists and is unverified, a verification email was sent.",
    )


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

    settings = get_settings()
    if (
        settings.auth.require_email_verification
        and user.email
        and not user.email_verified
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email address is not verified",
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
#         auth_service: AuthService = Depends(get_auth_service),  # noqa: ERA001
#         current_user: UserResponse = Depends(get_current_user)  # noqa: ERA001
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
#     return await auth_service.create_tenant(tenant_name, current_user.id)  # noqa: ERA001
