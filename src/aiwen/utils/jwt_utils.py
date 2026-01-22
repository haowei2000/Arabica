"""JWT utilities for token creation and verification."""

from datetime import UTC, datetime, timedelta, timezone
import logging
from typing import Dict, Optional

from jose import JWTError, jwt

from aiwen.config.factory import get_settings

# Default configuration

logger = logging.getLogger(__name__)
settings = get_settings()


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """
    Create a JWT access token.

    Args:
        data: The data to encode in the token
        expires_delta: The expiration time delta

    Returns:
        The encoded JWT token
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(
            minutes=settings.auth.access_token_expire_minutes
        )

    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(
        to_encode, settings.auth.jwt_secret_key, algorithm=settings.auth.jwt_algorithm
    )
    return encoded_jwt


def create_refresh_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """
    Create a JWT refresh token.

    Args:
        data: The data to encode in the token
        expires_delta: The expiration time delta

    Returns:
        The encoded JWT refresh token
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(
            days=settings.auth.refresh_token_expire_days
        )

    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(
        to_encode, settings.auth.jwt_secret_key, algorithm=settings.auth.jwt_algorithm
    )
    return encoded_jwt


def verify_token(token: str) -> dict | None:
    """
    Verify a JWT token and return the text_message.

    Args:
        token: The JWT token to verify

    Returns:
        The decoded text_message if valid, None otherwise
    """
    try:
        payload = jwt.decode(
            token,
            settings.auth.jwt_secret_key,
            algorithms=[settings.auth.jwt_algorithm],
        )
        logger.debug(f"Token decoded successfully: {payload.keys()}")
        return payload
    except JWTError as e:
        logger.warning(f"JWT verification failed: {type(e).__name__}: {e!s}")
        return None


def get_user_from_token(token: str) -> dict | None:
    """
    Extract user information from a JWT token.

    Args:
        token: The JWT token

    Returns:
        User information if token is valid, None otherwise
    """
    payload = verify_token(token)
    if payload is None:
        logger.warning("Token verification returned None")
        return None

    # Check if token has expired
    exp = payload.get("exp")
    if exp is not None:
        exp_time = datetime.fromtimestamp(exp, tz=UTC)
        now = datetime.now(UTC)
        if exp_time < now:
            logger.warning(f"Token has expired. Exp: {exp_time}, Now: {now}")
            return None

    user_data = {
        "user_id": payload.get("sub"),
        "role": payload.get("role"),
        "tenant_id": payload.get("tenant_id"),
    }
    logger.debug(
        f"Extracted user data: user_id={user_data['user_id']}, role={user_data['role']}"
    )
    return user_data
