"""Authentication middleware for validating JWT tokens."""

import logging

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from structure.schemas.auth.auth import TokenData
from structure.services.auth.token_service import TokenService

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware for authenticating requests using JWT tokens."""

    def __init__(self, app, exclude_paths: list = None, exclude_prefixes: list = None):
        """
        Initialize the AuthMiddleware.

        Args:
            app: The ASGI application
            exclude_paths: List of exact paths to exclude from authentication
            exclude_prefixes: List of path prefixes to exclude from authentication
        """
        super().__init__(app)
        self.exclude_paths = exclude_paths or [
            "/",
            "/docs",
            "/openapi.json",
            "/redoc",
            "/health",
            "/api/auth/login",
            "/api/auth/register",
            "/api/auth/refresh",
        ]
        self.exclude_prefixes = exclude_prefixes or ["/api/nl2sql"]

    async def dispatch(self, request: Request, call_next):
        """
        Process the incoming request and validate authentication.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware or endpoint handler

        Returns:
            HTTP response
        """
        # Skip authentication for excluded paths
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        # Skip authentication for excluded path prefixes
        for prefix in self.exclude_prefixes:
            if request.url.path.startswith(prefix):
                return await call_next(request)

        # Extract token from the Authorization header
        auth_header = request.headers.get("Authorization")
        logger.debug(
            f"Auth header for {request.url.path}: {auth_header[:50] if auth_header else 'None'}..."
        )

        if not auth_header or not auth_header.startswith("Bearer "):
            logger.warning(
                f"Missing or invalid authorization header for {request.url.path}"
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing or invalid authorization header"},
            )

        token = auth_header.split("Bearer ")[1]
        logger.debug(f"Extracted token: {token[:20]}...")

        # Verify token
        user_data = TokenService.verify_access_token(token)
        if not user_data:
            logger.warning(f"Token verification failed for {request.url.path}")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid or expired token"},
            )

        logger.debug(
            f"Token verified successfully for user_id: {user_data.get('user_id')}"
        )

        # Add user data to request state for use in endpoints
        request.state.user = TokenData(
            user_id=user_data["user_id"],
            role=user_data["role"],
            tenant_id=user_data["tenant_id"],
        )

        # Continue with the request
        response = await call_next(request)
        return response
