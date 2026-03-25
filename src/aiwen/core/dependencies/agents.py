"""Dependency injection functions for agent-related services."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.extensions.database import get_aiwen_db
from aiwen.middleware.cache_middleware import get_redis_client
from aiwen.services.app.app_crud import AppCRUD
from aiwen.services.context.context_crud import ContextCRUD
from aiwen.services.context.knowledge.document_crud import DocumentCRUD
from aiwen.services.context.knowledge.knowledge_crud import KnowledgeCRUD
from aiwen.services.context.skill_crud import SkillCRUD
from aiwen.services.context.tools.tool_crud import ToolCRUD
from aiwen.services.executor.executor_crud import ExecutorCRUD


async def get_app_crud(db: Annotated[AsyncSession, Depends(get_aiwen_db)]) -> AppCRUD:
    """
    Dependency to get AppCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        AppCRUD instance
    """
    return AppCRUD(db)


async def get_executor_crud(
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
) -> ExecutorCRUD:
    """
    Dependency to get ExecutorCRUD instance.
    """
    return ExecutorCRUD(db)


async def get_knowledge_crud(
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
) -> KnowledgeCRUD:
    """
    Dependency to get KnowledgeCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        KnowledgeCRUD instance
    """
    return KnowledgeCRUD(db)


async def get_context_crud(
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
) -> ContextCRUD:
    """
    Dependency to get ContextCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        ContextCRUD instance
    """
    return ContextCRUD(db)


async def get_document_crud(
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
) -> DocumentCRUD:
    """
    Dependency to get DocumentCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        DocumentCRUD instance
    """
    return DocumentCRUD(db)


async def get_tool_crud(
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
) -> ToolCRUD:
    """Dependency to get ToolCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        ToolCRUD instance
    """
    return ToolCRUD(db)


async def get_skill_crud(
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
) -> SkillCRUD:
    return SkillCRUD(db)


async def get_redis_client_dep():
    return get_redis_client(is_async=True)
