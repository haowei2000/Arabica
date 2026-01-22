"""Permission decorators for role-based access control."""

from functools import wraps
from typing import List

from fastapi import HTTPException, Request, status


def require_roles(roles: list[str]):
    """
    Decorator to require specific roles for accessing an endpoint.

    Args:
        roles: List of roles that are allowed to access the endpoint

    Returns:
        Decorated function
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract request object from args or kwargs
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break

            if request is None:
                for value in kwargs.values():
                    if isinstance(value, Request):
                        request = value
                        break

            if request is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Request object not found",
                )

            # Check if user is authenticated
            if not hasattr(request.state, "user") or not request.state.user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
                )

            # Check if user has required role
            user_role = request.state.user.role
            if user_role not in roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access denied. Required roles: {roles}",
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def require_auth(func):
    """
    Decorator to require authentication for accessing an endpoint.

    Args:
        func: Function to decorate

    Returns:
        Decorated function
    """

    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Extract request object from args or kwargs
        request = None
        for arg in args:
            if isinstance(arg, Request):
                request = arg
                break

        if request is None:
            for value in kwargs.values():
                if isinstance(value, Request):
                    request = value
                    break

        if request is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Request object not found",
            )

        # Check if user is authenticated
        if not hasattr(request.state, "user") or not request.state.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
            )

        return await func(*args, **kwargs)

    return wrapper
