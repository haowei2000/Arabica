"""
Dependencies module for FastAPI dependency injection.

Provides reusable dependencies for authentication, database sessions,
and other common requirements.
"""

from aiwen.dependencies.auth import (
    get_current_active_user,
    get_current_user,
    get_token_data,
    oauth2_scheme,
)

__all__ = [
    "get_current_active_user",
    "get_current_user",
    "get_token_data",
    "oauth2_scheme",
]
