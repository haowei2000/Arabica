"""CRUD operations for Message model."""

import builtins
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.agents.message import Message
from aiwen.schemas.agents.message import MessageContent


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

    async def create(
            self,
            app_id: str | UUID,
            conversation_id: str | UUID,
            query: str,
            message_content: list[MessageContent],
            answer: str,
            status: str,
            from_source: str | None = None,
            from_end_user_id: str | UUID | None = None,
            from_account_id: str | UUID | None = None,
            auto_commit: bool = False,
    ) -> Message:
        """
        Create a new message.

        Args:
            app_id: Application ID
            conversation_id: Conversation ID
            query: Query content
            message_content: Message content
            answer: Answer content
            status: Status of the message
            from_source: Source of the message
            from_end_user_id: End user ID if from end user
            from_account_id: Account ID if from account
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            Created Message instance
        """
        # Normalize UUID fields to strings
        app_id = normalize_uuid_to_str(app_id)
        conversation_id = normalize_uuid_to_str(conversation_id)
        from_end_user_id = (
            normalize_uuid_to_str(from_end_user_id) if from_end_user_id else None
        )
        from_account_id = (
            normalize_uuid_to_str(from_account_id) if from_account_id else None
        )

        message_obj = Message(
            app_id=app_id,
            conversation_id=conversation_id,
            query=query,
            message=[message_content.model_dump() for message_content in message_content],
            answer=answer,
            status=status,
            from_source=from_source,
            from_end_user_id=from_end_user_id,
            from_account_id=from_account_id,
        )

        self.db.add(message_obj)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(message_obj)
        return message_obj

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
            *,
            query: str | None = None,
            message_content: str | None = None,
            answer: str | None = None,
            status: str | None = None,
            from_source: str | None = None,
            from_end_user_id: str | UUID | None = None,
            from_account_id: str | UUID | None = None,
            auto_commit: bool = False,
    ) -> Message | None:
        """
        Update an existing message_content.

        Args:
            message_id: The message_content ID to update
            query: New query content
            message_content: New message_content content
            answer: New answer content
            status: New status of the message_content
            from_source: New source of the message_content
            from_end_user_id: New end user ID
            from_account_id: New account ID
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            Updated Message instance or None if not found
        """
        normalized_id = normalize_uuid_to_str(message_id)
        message = await self.get_by_id(normalized_id)
        if not message:
            return None

        # Prepare update data based on provided parameters
        update_data = {}
        if query is not None:
            update_data["query"] = query
        if message_content is not None:
            update_data["message_content"] = message_content
        if answer is not None:
            update_data["answer"] = answer
        if status is not None:
            update_data["status"] = status
        if from_source is not None:
            update_data["from_source"] = from_source
        if from_end_user_id is not None:
            update_data["from_end_user_id"] = normalize_uuid_to_str(from_end_user_id)
        if from_account_id is not None:
            update_data["from_account_id"] = normalize_uuid_to_str(from_account_id)

        # Apply updates
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
            limit: int = 100,
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
            count_stmt = count_stmt.where(
                Message.conversation_id == normalized_conversation_id
            )

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
            count_stmt = count_stmt.where(
                Message.from_end_user_id == normalized_from_end_user_id
            )

        if from_account_id:
            normalized_from_account_id = normalize_uuid_to_str(from_account_id)
            stmt = stmt.where(Message.from_account_id == normalized_from_account_id)
            count_stmt = count_stmt.where(
                Message.from_account_id == normalized_from_account_id
            )

        # Get total count
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Apply pagination and ordering (oldest first for chronological order)
        stmt = stmt.order_by(Message.created_at.asc()).offset(skip).limit(limit)

        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def search(
            self,
            search_term: str,
            conversation_id: str | UUID | None = None,
            app_id: str | UUID | None = None,
            skip: int = 0,
            limit: int = 100,
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
                Message.answer.ilike(search_pattern),
            )
        )
        count_stmt = select(func.count(Message.id)).where(
            or_(
                Message.query.ilike(search_pattern),
                Message.answer.ilike(search_pattern),
            )
        )

        if conversation_id:
            normalized_conversation_id = normalize_uuid_to_str(conversation_id)
            stmt = stmt.where(Message.conversation_id == normalized_conversation_id)
            count_stmt = count_stmt.where(
                Message.conversation_id == normalized_conversation_id
            )

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
            self, conversation_id: str | UUID, skip: int = 0, limit: int = 100
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
        return await self.list(conversation_id=conversation_id, skip=skip, limit=limit)
