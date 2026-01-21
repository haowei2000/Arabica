"""CRUD operations for Message model."""

import builtins
from typing import List, Optional, Tuple, Union
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.agents.message import Message
from aiwen.schemas.agents.message import MessageCreate, MessageUpdate


def normalize_uuid_to_str(val: str | UUID) -> str:
    """
    Normalize a UUID value to string.
    
    Args:
        val: UUID object or string representation
        
    Returns:
        String representation of the UUID
    """
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, str):
        # Validate that it's a valid UUID string
        try:
            UUID(val)  # This will raise ValueError if invalid
            return val
        except ValueError as e:
            raise ValueError(f"Invalid UUID string: {val}") from e
    raise TypeError(f"Expected UUID or str, got {type(val)}")


class MessageCRUD:
    """CRUD operations for Message model."""

    def __init__(self, db_session: AsyncSession):
        """
        Initialize MessageCRUD with database session.

        Args:
            db_session: SQLAlchemy async session
        """
        self.db = db_session

    async def create(self, data: MessageCreate, auto_commit: bool = False) -> Message:
        """
        Create a new input.

        Args:
            data: Message creation data
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            Created Message instance
        """
        # Normalize UUID fields to strings
        app_id = data.app_id
        conversation_id = data.conversation_id
        from_end_user_id =data.from_end_user_id if data.from_end_user_id else None
        from_account_id = data.from_account_id if data.from_account_id else None

        message = Message(
            app_id=app_id,
            conversation_id=conversation_id,
            query=data.query,
            message=data.message,
            answer=data.answer,
            status=data.status,
            from_source=data.from_source,
            from_end_user_id=from_end_user_id,
            from_account_id=from_account_id,
        )

        self.db.add(message)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(message)
        return message

    async def get_by_id(self, message_id: str | UUID) -> Message | None:
        """
        Get input by ID.

        Args:
            message_id: The input ID to search for

        Returns:
            Message instance or None if not found
        """
        normalized_id = normalize_uuid_to_str(message_id)
        stmt = select(Message).where(Message.id == normalized_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def update(
        self,
        message_id: str | UUID,
        data: MessageUpdate,
        auto_commit: bool = False
    ) -> Message | None:
        """
        Update an existing input.

        Args:
            message_id: The input ID to update
            data: Update data
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            Updated Message instance or None if not found
        """
        normalized_id = normalize_uuid_to_str(message_id)
        message = await self.get_by_id(normalized_id)
        if not message:
            return None

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(message, key, value)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(message)
        return message

    async def list(
        self,
        conversation_id: str | UUID | None = None,
        app_id: str | UUID | None = None,
        status: str | None = None,
        from_end_user_id: str | UUID | None = None,
        from_account_id: str | UUID | None = None,
        skip: int = 0,
        limit: int = 100
    ) -> tuple[list[Message], int]:
        """
        List messages with filtering and pagination.

        Args:
            conversation_id: Filter by conversation ID
            app_id: Filter by application ID
            status: Filter by status
            from_end_user_id: Filter by end user ID
            from_account_id: Filter by account ID
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of messages, total count)
        """
        # Base query
        stmt = select(Message)
        count_stmt = select(func.count(Message.id))

        # Apply filters
        if conversation_id:
            normalized_conversation_id = normalize_uuid_to_str(conversation_id)
            stmt = stmt.where(Message.conversation_id == normalized_conversation_id)
            count_stmt = count_stmt.where(Message.conversation_id == normalized_conversation_id)

        if app_id:
            normalized_app_id = normalize_uuid_to_str(app_id)
            stmt = stmt.where(Message.app_id == normalized_app_id)
            count_stmt = count_stmt.where(Message.app_id == normalized_app_id)

        if status:
            stmt = stmt.where(Message.status == status)
            count_stmt = count_stmt.where(Message.status == status)

        if from_end_user_id:
            normalized_from_end_user_id = normalize_uuid_to_str(from_end_user_id)
            stmt = stmt.where(Message.from_end_user_id == normalized_from_end_user_id)
            count_stmt = count_stmt.where(Message.from_end_user_id == normalized_from_end_user_id)

        if from_account_id:
            normalized_from_account_id = normalize_uuid_to_str(from_account_id)
            stmt = stmt.where(Message.from_account_id == normalized_from_account_id)
            count_stmt = count_stmt.where(Message.from_account_id == normalized_from_account_id)

        # Get total count
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Apply pagination and ordering (newest first)
        stmt = stmt.order_by(Message.created_at.desc()).offset(skip).limit(limit)

        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def search(
        self,
        search_term: str,
        conversation_id: str | UUID | None = None,
        app_id: str | UUID | None = None,
        skip: int = 0,
        limit: int = 100
    ) -> tuple[builtins.list[Message], int]:
        """
        Full-text search in messages (query and answer fields).

        Args:
            search_term: Term to search for
            conversation_id: Filter by conversation ID
            app_id: Filter by application ID
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of messages, total count)
        """
        # Search in query and answer fields using ILIKE
        search_pattern = f"%{search_term}%"
        stmt = select(Message).where(
            or_(
                Message.query.ilike(search_pattern),
                Message.answer.ilike(search_pattern)
            )
        )
        count_stmt = select(func.count(Message.id)).where(
            or_(
                Message.query.ilike(search_pattern),
                Message.answer.ilike(search_pattern)
            )
        )

        if conversation_id:
            normalized_conversation_id = normalize_uuid_to_str(conversation_id)
            stmt = stmt.where(Message.conversation_id == normalized_conversation_id)
            count_stmt = count_stmt.where(Message.conversation_id == normalized_conversation_id)

        if app_id:
            normalized_app_id = normalize_uuid_to_str(app_id)
            stmt = stmt.where(Message.app_id == normalized_app_id)
            count_stmt = count_stmt.where(Message.app_id == normalized_app_id)

        # Get total
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Get results
        stmt = stmt.order_by(Message.created_at.desc()).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def get_by_conversation(
        self,
        conversation_id: str | UUID,
        skip: int = 0,
        limit: int = 100
    ) -> tuple[builtins.list[Message], int]:
        """
        Get all messages for a specific conversation.

        Args:
            conversation_id: The conversation ID
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of messages, total count)
        """
        return await self.list(
            conversation_id=conversation_id,
            skip=skip,
            limit=limit
        )
