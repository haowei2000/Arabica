"""Dependency injection functions for agent-related services."""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.extensions.database import get_aiwen_db
from aiwen.middleware.cache_middleware import get_redis_client
from aiwen.services.agent.runtime import AgentRuntime
from aiwen.services.crud.agent_template_crud import AgentTemplateCRUD
from aiwen.services.crud.app_crud import AppCRUD
from aiwen.services.crud.context_crud import ContextCRUD
from aiwen.services.crud.conversation_crud import ConversationCRUD
from aiwen.services.crud.document_crud import DocumentCRUD
from aiwen.services.crud.knowledge_crud import KnowledgeCRUD
from aiwen.services.crud.message_crud import MessageCRUD
from aiwen.services.crud.task_crud import AgentTaskCRUD
from aiwen.workers.task_consumer import AgentTaskConsumer
from aiwen.workers.task_producer import AgentTaskProducer


async def get_app_crud(db: AsyncSession = Depends(get_aiwen_db)) -> AppCRUD:
    """
    Dependency to get AppCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        AppCRUD instance
    """
    return AppCRUD(db)


async def get_conversation_crud(
    db: AsyncSession = Depends(get_aiwen_db),
) -> ConversationCRUD:
    """
    Dependency to get ConversationCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        ConversationCRUD instance
    """
    return ConversationCRUD(db)


async def get_message_crud(db: AsyncSession = Depends(get_aiwen_db)) -> MessageCRUD:
    """
    Dependency to get MessageCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        MessageCRUD instance
    """
    return MessageCRUD(db)


async def get_template_crud(
    db: AsyncSession = Depends(get_aiwen_db),
) -> AgentTemplateCRUD:
    """
    Dependency to get AgentTemplateCRUD instance.
    """
    return AgentTemplateCRUD(db)


async def get_task_crud(
    db: AsyncSession = Depends(get_aiwen_db),
) -> AgentTaskCRUD:
    return AgentTaskCRUD(db)


async def get_knowledge_crud(
        db: AsyncSession = Depends(get_aiwen_db),
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
        db: AsyncSession = Depends(get_aiwen_db),
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
        db: AsyncSession = Depends(get_aiwen_db),
) -> DocumentCRUD:
    """
    Dependency to get DocumentCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        DocumentCRUD instance
    """
    return DocumentCRUD(db)


async def get_redis_client_dep():
    return get_redis_client(is_async=True)


# 全局 AgentRuntime 单例
_agent_runtime = AgentRuntime()


def get_agent_runtime() -> AgentRuntime:
    """
    Get the global AgentRuntime instance.

    Returns:
        Global AgentRuntime singleton
    """
    return _agent_runtime


async def get_task_producer(
    redis_client=Depends(get_redis_client_dep),
) -> AgentTaskProducer:
    """
    Get AgentTaskProducer instance.

    Args:
        redis_client: Redis async client from dependency injection

    Returns:
        AgentTaskProducer instance
    """
    return AgentTaskProducer(redis_client)


async def get_task_consumer(
    redis_client=Depends(get_redis_client_dep),
) -> AgentTaskConsumer:
    """
    Get AgentTaskConsumer instance.

    Args:
        redis_client: Redis async client from dependency injection

    Returns:
        AgentTaskConsumer instance
    """
    return AgentTaskConsumer(redis_client)
