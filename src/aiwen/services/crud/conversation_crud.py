"""CRUD operations for Conversation model."""

import builtins
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aiwen.models.agents.conversation import Conversation
from aiwen.schemas.agents.conversation import ConversationCreate, ConversationUpdate


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


class ConversationCRUD:
    """CRUD operations for Conversation model."""

    def __init__(self, db_session: AsyncSession):
        """
        Initialize ConversationCRUD with database session.

        Args:
            db_session: SQLAlchemy async session
        """
        self.db = db_session

    async def create(
        self, data: ConversationCreate, auto_commit: bool = False
    ) -> Conversation:
        """
        Create a new conversation.

        Args:
            data: Conversation creation data
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            Created Conversation instance
        """
        # Convert schema to dict and handle field name mapping
        data_dict = data.model_dump()

        data_dict["app_id"] = normalize_uuid_to_str(data.app_id)
        if data.from_account_id:
            data_dict["account_id"] = normalize_uuid_to_str(data.from_account_id)
        if data.from_end_user_id:
            data_dict["from_end_user_id"] = normalize_uuid_to_str(data.from_end_user_id)

        # Handle optional ID (if provided, use it; otherwise let the model generate one)
        if data.id:
            data_dict["id"] = normalize_uuid_to_str(data.id)

        # Remove API layer field names that don't exist in the model
        if "from_account_id" in data_dict:
            del data_dict["from_account_id"]

        conversation = Conversation(**data_dict, dialogue_count=0, is_deleted=False)

        self.db.add(conversation)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        # Removed unnecessary refresh - object already has all fields after flush
        return conversation

    async def get_by_id(
        self, conversation_id: str | UUID, include_deleted: bool = False
    ) -> Conversation | None:
        """
        Get conversation by ID.

        Args:
            conversation_id: The conversation ID to search for
            include_deleted: Whether to include deleted conversations

        Returns:
            Conversation instance or None if not found
        """
        normalized_id = normalize_uuid_to_str(conversation_id)
        stmt = select(Conversation).where(Conversation.id == normalized_id)

        if not include_deleted:
            stmt = stmt.where(Conversation.is_deleted == False)

        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_with_messages(
        self, conversation_id: str | UUID, include_deleted: bool = False
    ) -> Conversation | None:
        """
        Get conversation by ID with messages eagerly loaded.

        Args:
            conversation_id: The conversation ID to search for
            include_deleted: Whether to include deleted conversations

        Returns:
            Conversation instance with messages or None if not found
        """
        normalized_id = normalize_uuid_to_str(conversation_id)
        stmt = (
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.id == normalized_id)
        )

        if not include_deleted:
            stmt = stmt.where(Conversation.is_deleted == False)

        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def update(
        self,
        conversation_id: str | UUID,
        data: ConversationUpdate,
        auto_commit: bool = False,
    ) -> Conversation | None:
        """
        Update an existing conversation.

        Args:
            conversation_id: The conversation ID to update
            data: Update data
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            Updated Conversation instance or None if not found
        """
        normalized_id = normalize_uuid_to_str(conversation_id)
        conversation = await self.get_by_id(normalized_id)
        if not conversation:
            return None

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(conversation, key, value)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(conversation)
        return conversation

    async def soft_delete(
        self, conversation_id: str | UUID, auto_commit: bool = False
    ) -> bool:
        """
        Soft delete a conversation by setting is_deleted=True.

        Args:
            conversation_id: The conversation ID to delete
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            True if deleted, False if not found
        """
        normalized_id = normalize_uuid_to_str(conversation_id)
        conversation = await self.get_by_id(normalized_id)
        if not conversation:
            return False

        conversation.is_deleted = True
        conversation.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        return True

    async def list(
        self,
        app_id: str | UUID | None = None,
        status: str | None = None,
        from_end_user_id: str | UUID | None = None,
        from_account_id: str | UUID | None = None,
        skip: int = 0,
        limit: int = 100,
        include_deleted: bool = False,
    ) -> tuple[list[Conversation], int]:
        """
        List conversations with filtering and pagination.

        Args:
            app_id: Filter by application ID
            status: Filter by status
            from_end_user_id: Filter by end user ID
            from_account_id: Filter by account ID
            skip: Number of records to skip
            limit: Maximum number of records to return
            include_deleted: Whether to include deleted conversations

        Returns:
            Tuple of (list of conversations, total count)
        """
        # Base query
        stmt = select(Conversation)
        count_stmt = select(func.count(Conversation.id))

        # Apply filters
        if app_id:
            normalized_app_id = normalize_uuid_to_str(app_id)
            stmt = stmt.where(Conversation.app_id == normalized_app_id)
            count_stmt = count_stmt.where(Conversation.app_id == normalized_app_id)

        if status:
            stmt = stmt.where(Conversation.status == status)
            count_stmt = count_stmt.where(Conversation.status == status)

        if from_end_user_id:
            normalized_from_end_user_id = normalize_uuid_to_str(from_end_user_id)
            stmt = stmt.where(
                Conversation.from_end_user_id == normalized_from_end_user_id
            )
            count_stmt = count_stmt.where(
                Conversation.from_end_user_id == normalized_from_end_user_id
            )

        if from_account_id:
            normalized_from_account_id = normalize_uuid_to_str(from_account_id)
            stmt = stmt.where(Conversation.account_id == normalized_from_account_id)
            count_stmt = count_stmt.where(
                Conversation.account_id == normalized_from_account_id
            )

        if not include_deleted:
            stmt = stmt.where(Conversation.is_deleted == False)
            count_stmt = count_stmt.where(Conversation.is_deleted == False)

        # Get total count
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Apply pagination and ordering
        stmt = stmt.order_by(Conversation.created_at.desc()).offset(skip).limit(limit)

        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def search(
        self,
        search_term: str,
        app_id: str | UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[builtins.list[Conversation], int]:
        """
        Full-text search in conversations (name and summary fields).

        Args:
            search_term: Term to search for
            app_id: Filter by application ID
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of conversations, total count)
        """
        # Search in name and summary fields using ILIKE
        search_pattern = f"%{search_term}%"
        stmt = select(Conversation).where(
            or_(
                Conversation.name.ilike(search_pattern),
                Conversation.summary.ilike(search_pattern),
            )
        )
        count_stmt = select(func.count(Conversation.id)).where(
            or_(
                Conversation.name.ilike(search_pattern),
                Conversation.summary.ilike(search_pattern),
            )
        )

        if app_id:
            normalized_app_id = normalize_uuid_to_str(app_id)
            stmt = stmt.where(Conversation.app_id == normalized_app_id)
            count_stmt = count_stmt.where(Conversation.app_id == normalized_app_id)

        # Exclude deleted
        stmt = stmt.where(Conversation.is_deleted == False)
        count_stmt = count_stmt.where(Conversation.is_deleted == False)

        # Get total
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Get results
        stmt = stmt.order_by(Conversation.updated_at.desc()).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def increment_dialogue_count(
        self, conversation_id: str | UUID, auto_commit: bool = False
    ) -> Conversation | None:
        """
        Increment the dialogue count for a conversation.

        Args:
            conversation_id: The conversation ID
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            Updated Conversation instance or None if not found
        """
        normalized_id = normalize_uuid_to_str(conversation_id)
        conversation = await self.get_by_id(normalized_id)
        if not conversation:
            return None

        conversation.dialogue_count += 1
        conversation.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(conversation)
        return conversation
