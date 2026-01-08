#!/usr/bin/env python3
"""
Memory Service for Default Agent Template

Provides conversation history loading and formatting for the default agent.
Loads messages from database and converts them to dictionary format.

默认Agent模版的Memory服务
从数据库加载对话历史并格式化为字典格式
"""

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select

from aiwen.extensions.database import get_session
from aiwen.models.agents.message import Message
from aiwen.schemas.agents.message import MessageCreate
from aiwen.services.agents.crud.message_crud import MessageCRUD

logger = logging.getLogger(__name__)


async def add_message_to_history(
    message: dict,
    conversation_id: UUID,
    app_id: Optional[UUID] = None
):
    """
    Add a message to the memory.

    Ensures the conversation exists before adding the message.
    If the conversation doesn't exist, it will be created automatically.

    Args:
        message: Message dictionary to add to memory
        conversation_id: UUID of the conversation
        app_id: Optional UUID of the app/agent (used when creating conversation)
    """
    from aiwen.services.agents.crud.conversation_crud import ConversationCRUD
    from aiwen.schemas.agents.conversation import ConversationCreate
    from aiwen.utils.time import utc_now

    async with get_session("aiwen") as db:
        # First, ensure the conversation exists
        conversation_crud = ConversationCRUD(db)
        conversation = await conversation_crud.get_by_id(conversation_id)

        if not conversation:
            logger.warning(
                f"Conversation {conversation_id} not found. "
                f"Creating new conversation for message history."
            )

            # Ensure we have an app_id for creating the conversation
            if not app_id:
                raise ValueError(
                    f"Cannot create conversation {conversation_id}: "
                    f"conversation does not exist and no app_id provided"
                )

            # Create a new conversation if it doesn't exist, using the provided conversation_id
            conversation = await conversation_crud.create(
                ConversationCreate(
                    id=conversation_id,  # Use the provided conversation_id
                    app_id=app_id,
                    name=f"Auto-created conversation - {utc_now().strftime('%Y-%m-%d %H:%M')}",
                    status="normal",
                    from_source="system",
                ),
                auto_commit=False
            )
            logger.info(f"Created conversation {conversation.id} with app_id {app_id}")

        # Now add the message
        crud = MessageCRUD(db)
        await crud.create(
            MessageCreate(
                conversation_id=conversation_id,
                message=message,
                app_id=app_id or conversation.app_id  # Use app_id from conversation if not provided
            )
        )
        logger.debug(f"Added message to memory: {message}")


async def get_messages_from_history(
        conversation_id: Optional[UUID],
        max_messages: int = 20
) -> list[dict]:
    """
    Load conversation history from database by conversation ID.

    Loads the most recent messages from the conversation and converts them
    to dictionary format (with 'role' and 'content' keys).

    Args:
        conversation_id: UUID of the conversation to load history from
        max_messages: Maximum number of historical messages to load (default: 20)

    Returns:
        List of messages in chronological order as dictionaries

    从数据库按对话ID加载对话历史。
    加载对话中最近的消息并转换为字典格式。

    参数:
        conversation_id: 要加载历史记录的对话的UUID
        max_messages: 要加载的历史消息的最大数量（默认：20）

    返回:
        按时间顺序排列的消息字典列表
    """
    if not conversation_id:
        logger.info("No conversation_id provided, returning empty history")
        return []

    try:
        async with get_session('aiwen') as db:
            # Query messages for this conversation, ordered by created_at
            stmt = (
                select(Message.message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(max_messages)
            )

            result = await db.execute(stmt)
            messages = result.scalars().all()

            if not messages:
                logger.debug(f"No messages found for conversation {conversation_id}")
                return []

            # Reverse to get chronological order (oldest first)
            messages = list(reversed(messages))

            logger.info(
                f"Loaded {len(messages)} message pairs "
                f"for conversation {conversation_id}"
            )

            return messages

    except Exception as e:
        logger.error(f"Error loading conversation history: {e}", exc_info=True)
        return []
