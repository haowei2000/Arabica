"""Security utilities for password hashing and verification."""
from typing import Union

import bcrypt


def hash_password(password: str | bytes) -> str:
    """
    Hash a password using bcrypt.

    Args:
        password: The password to hash (string or bytes)

    Returns:
        The hashed password as a string
    """
    if isinstance(password, str):
        password = password.encode('utf-8')

    # Generate salt and hash the password
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password, salt)

    # Return as string for storage
    return hashed.decode('utf-8')


def verify_password(plain_password: str | bytes, hashed_password: str | bytes) -> bool:
    """
    Verify a plain password against a hashed password.

    Args:
        plain_password: The plain password to verify
        hashed_password: The hashed password to compare against

    Returns:
        True if passwords match, False otherwise
    """
    if isinstance(plain_password, str):
        plain_password = plain_password.encode('utf-8')
    if isinstance(hashed_password, str):
        hashed_password = hashed_password.encode('utf-8')

    return bcrypt.checkpw(plain_password, hashed_password)


def generate_secret_key() -> str:
    """
    Generate a random secret key for JWT signing.

    Returns:
        A randomly generated secret key
    """
    import secrets
    return secrets.token_urlsafe(32)
