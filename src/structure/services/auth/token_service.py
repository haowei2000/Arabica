"""Token service for JWT token management."""

from datetime import timedelta
from uuid import UUID

from structure.utils.jwt_utils import (
    create_access_token,
    create_refresh_token,
    get_user_from_token,
)


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
