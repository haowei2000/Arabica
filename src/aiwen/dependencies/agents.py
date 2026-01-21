"""Dependency injection functions for agent-related services."""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.extensions.database import get_aiwen_db
from aiwen.middleware.cache_middleware import get_redis_client
from aiwen.services.agents.crud.agent_template_crud import AgentTemplateCRUD
from aiwen.services.agents.crud.app_crud import AppCRUD
from aiwen.services.agents.crud.conversation_crud import ConversationCRUD
from aiwen.services.agents.crud.message_crud import MessageCRUD
from aiwen.services.agents.crud.task_crud import AgentTaskCRUD
from aiwen.services.agents.runtime import AgentRuntime
from aiwen.workers.task_consumer import AgentTaskConsumer
from aiwen.workers.task_producer import AgentTaskProducer


async def get_app_crud(
        db: AsyncSession = Depends(get_aiwen_db)
) -> AppCRUD:
    """
    Dependency to get AppCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        AppCRUD instance
    """
    return AppCRUD(db)


async def get_conversation_crud(
        db: AsyncSession = Depends(get_aiwen_db)
) -> ConversationCRUD:
    """
    Dependency to get ConversationCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        ConversationCRUD instance
    """
    return ConversationCRUD(db)


async def get_message_crud(
        db: AsyncSession = Depends(get_aiwen_db)
) -> MessageCRUD:
    """
    Dependency to get MessageCRUD instance.

    Args:
        db: Database session from dependency injection

    Returns:
        MessageCRUD instance
    """
    return MessageCRUD(db)


async def get_template_crud(
        db: AsyncSession = Depends(get_aiwen_db)
) -> AgentTemplateCRUD:
    """
    Dependency to get AgentTemplateCRUD instance.
    """
    return AgentTemplateCRUD(db)




async def get_task_crud(
        db: AsyncSession = Depends(get_aiwen_db),
) -> AgentTaskCRUD:
    return AgentTaskCRUD(db)


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
