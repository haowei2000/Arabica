"""Token service for JWT token management."""

from datetime import UTC, datetime, timedelta
import logging
from uuid import UUID

from jose import JWTError, jwt

from structure.config.factory import get_settings
from structure.utils.jwt_utils import (
    create_access_token,
    create_refresh_token,
    get_user_from_token,
)

logger = logging.getLogger(__name__)


class TokenService:
    """Service for handling JWT token operations."""

    @staticmethod
    def create_tokens(user_id: UUID, role: str, tenant_id: UUID) -> tuple[str, str]:
        """
        Create access and refresh tokens for a user.

        Args:
            user_id: User ID
            role: User role
            tenant_id: Tenant ID

        Returns:
            Tuple of (access_token, refresh_token)
        """
        # Token data
        token_data = {"sub": str(user_id), "role": role, "tenant_id": str(tenant_id)}

        # Create access token (30 minutes expiry)
        access_token = create_access_token(
            data=token_data, expires_delta=timedelta(minutes=30)
        )

        # Create refresh token (7 days expiry)
        refresh_token = create_refresh_token(
            data=token_data, expires_delta=timedelta(days=7)
        )

        return access_token, refresh_token

    @staticmethod
    def refresh_tokens(refresh_token: str) -> tuple[str, str] | None:
        """
        Refresh access and refresh tokens using a refresh token.

        Args:
            refresh_token: Refresh token

        Returns:
            Tuple of (new_access_token, new_refresh_token) if valid, None otherwise
        """
        # Verify refresh token
        user_data = get_user_from_token(refresh_token)
        if not user_data:
            return None

        # Create new tokens
        new_access_token, new_refresh_token = TokenService.create_tokens(
            user_id=UUID(user_data["user_id"]),
            role=user_data["role"],
            tenant_id=UUID(user_data["tenant_id"]),
        )

        return new_access_token, new_refresh_token

    @staticmethod
    def verify_access_token(access_token: str) -> dict | None:
        """
        Verify an access token and extract user data.

        Args:
            access_token: Access token to verify

        Returns:
            User data if token is valid, None otherwise
        """
        return get_user_from_token(access_token)

    @staticmethod
    def create_email_verification_token(user_id: UUID, email: str) -> str:
        """Create a short-lived token for email verification."""
        settings = get_settings()
        expire = datetime.now(UTC) + timedelta(
            minutes=settings.email.verification_token_expire_minutes
        )
        token_data = {
            "sub": str(user_id),
            "email": email,
            "purpose": "email_verification",
            "exp": expire,
        }
        return jwt.encode(
            token_data,
            settings.auth.jwt_secret_key,
            algorithm=settings.auth.jwt_algorithm,
        )

    @staticmethod
    def verify_email_verification_token(token: str) -> dict | None:
        """Decode and validate an email verification token."""
        settings = get_settings()
        try:
            payload = jwt.decode(
                token,
                settings.auth.jwt_secret_key,
                algorithms=[settings.auth.jwt_algorithm],
            )
        except JWTError as exc:
            logger.warning("Email verification token failed: %s", exc)
            return None

        if payload.get("purpose") != "email_verification":
            return None

        user_id = payload.get("sub")
        email = payload.get("email")
        if not user_id or not email:
            return None

        return {"user_id": user_id, "email": email}
