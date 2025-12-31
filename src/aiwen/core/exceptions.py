"""Global exception handlers for FastAPI application."""

import logging
from typing import Union

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from aiwen.config.factory import get_settings
from aiwen.schemas.common import error

logger = logging.getLogger(__name__)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle request validation errors (422)."""
    try:
        body = await request.json()
    except Exception:
        body = None

    logger.error("Request validation error: %s \n Data: %s", exc, body)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=error(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="请求参数验证失败",
            detail=exc.errors(),
        ).model_dump(),
    )


async def sqlalchemy_exception_handler(
    request: Request, exc: SQLAlchemyError
) -> JSONResponse:
    """Handle SQLAlchemy database errors."""
    settings = get_settings()
    logger.error("Database error: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="数据库操作失败",
            detail=str(exc) if settings.DEBUG else "数据库内部错误",
        ).model_dump(),
    )


async def response_validation_exception_handler(
    request: Request, exc: ResponseValidationError
) -> JSONResponse:
    """Handle response validation errors."""
    settings = get_settings()
    logger.error("Response validation error: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="服务器响应数据格式错误",
            detail=exc.errors() if settings.DEBUG else "响应数据格式验证失败",
        ).model_dump(),
    )


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Handle ValueError exceptions."""
    settings = get_settings()
    logger.exception("ValueError occurred: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="参数错误",
            detail=str(exc) if settings.DEBUG else "请求参数有误",
        ).model_dump(),
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle all uncaught exceptions."""
    settings = get_settings()
    logger.exception("Unhandled exception occurred: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="服务器内部错误",
            detail=str(exc) if settings.DEBUG else "服务器遇到了意外情况",
        ).model_dump(),
    )


def register_exception_handlers(app) -> None:
    """Register all exception handlers to the FastAPI app."""
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
    app.add_exception_handler(ResponseValidationError, response_validation_exception_handler)
    app.add_exception_handler(ValueError, value_error_handler)
    app.add_exception_handler(Exception, general_exception_handler)

    logger.info("Exception handlers registered successfully")
