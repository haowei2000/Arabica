"""CRUD operations for Knowledge model."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.context.knowledge.knowledge import Knowledge
from aiwen.schemas.context.knowledge.knowledge import KnowledgeCreate, KnowledgeUpdate
from aiwen.services.context.context_syncer import ContextSyncer


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
        try:
            UUID(val)
            return val
        except ValueError as e:
            raise ValueError(f"Invalid UUID string: {val}") from e
    raise TypeError(f"Expected UUID or str, got {type(val)}")


class KnowledgeCRUD:
    """CRUD operations for Knowledge model."""

    def __init__(self, db_session: AsyncSession):
        """
        Initialize KnowledgeCRUD with database session.

        Args:
            db_session: SQLAlchemy async session
        """
        self.db = db_session

    async def create(
        self,
        data: KnowledgeCreate,
        user_id: str | UUID,
        auto_commit: bool = False,
    ) -> Knowledge:
        """
        Create a new knowledge base.

        Args:
            data: Knowledge creation data
            user_id: ID of the user creating the knowledge base
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            Created Knowledge instance
        """
        data_dict = data.model_dump()
        data_dict["user_id"] = normalize_uuid_to_str(user_id)
        data_dict["owner_id"] = normalize_uuid_to_str(user_id)

        if data.preprocess_id:
            data_dict["preprocess_id"] = normalize_uuid_to_str(data.preprocess_id)

        knowledge = Knowledge(**data_dict)
        self.db.add(knowledge)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(knowledge)
        await ContextSyncer(self.db).sync_knowledge(knowledge)
        return knowledge

    async def get_by_id(self, knowledge_id: str | UUID) -> Knowledge | None:
        """
        Get knowledge base by ID.

        Args:
            knowledge_id: The knowledge base ID to search for

        Returns:
            Knowledge instance or None if not found
        """
        normalized_id = normalize_uuid_to_str(knowledge_id)
        stmt = select(Knowledge).where(Knowledge.id == normalized_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str, user_id: str | UUID) -> Knowledge | None:
        """
        Get knowledge base by name for a specific user.

        Args:
            name: The knowledge base name
            user_id: The user ID

        Returns:
            Knowledge instance or None if not found
        """
        normalized_user_id = normalize_uuid_to_str(user_id)
        stmt = select(Knowledge).where(
            Knowledge.name == name,
            Knowledge.user_id == normalized_user_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def update(
        self,
        knowledge_id: str | UUID,
        data: KnowledgeUpdate,
        auto_commit: bool = False,
    ) -> Knowledge | None:
        """
        Update an existing knowledge base.

        Args:
            knowledge_id: The knowledge base ID to update
            data: Update data
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            Updated Knowledge instance or None if not found
        """
        knowledge = await self.get_by_id(knowledge_id)
        if not knowledge:
            return None

        update_data = data.model_dump(exclude_unset=True)

        if update_data.get("preprocess_id"):
            update_data["preprocess_id"] = normalize_uuid_to_str(
                update_data["preprocess_id"]
            )

        for key, value in update_data.items():
            setattr(knowledge, key, value)

        knowledge.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(knowledge)
        await ContextSyncer(self.db).sync_knowledge(knowledge)
        return knowledge

    async def delete(self, knowledge_id: str | UUID, auto_commit: bool = False) -> bool:
        """
        Delete a knowledge base.

        Args:
            knowledge_id: The knowledge base ID to delete
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            True if deleted, False if not found
        """
        knowledge = await self.get_by_id(knowledge_id)
        if not knowledge:
            return False

        await ContextSyncer(self.db).remove_knowledge(knowledge)
        await self.db.delete(knowledge)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        return True

    async def list(
        self,
        user_id: str | UUID | None = None,
        status: str | None = None,
        permission: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Knowledge], int]:
        """
        List knowledge bases with filtering and pagination.

        Args:
            user_id: Filter by user ID
            status: Filter by status
            permission: Filter by permission
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of knowledge bases, total count)
        """
        stmt = select(Knowledge)
        count_stmt = select(func.count(Knowledge.id))

        if user_id:
            normalized_user_id = normalize_uuid_to_str(user_id)
            stmt = stmt.where(Knowledge.user_id == normalized_user_id)
            count_stmt = count_stmt.where(Knowledge.user_id == normalized_user_id)

        if status:
            stmt = stmt.where(Knowledge.status == status)
            count_stmt = count_stmt.where(Knowledge.status == status)

        if permission:
            stmt = stmt.where(Knowledge.permission == permission)
            count_stmt = count_stmt.where(Knowledge.permission == permission)

        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        stmt = stmt.order_by(Knowledge.created_at.desc()).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def search(
        self,
        search_term: str,
        user_id: str | UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Knowledge], int]:
        """
        Search knowledge bases by name or description.

        Args:
            search_term: Term to search for
            user_id: Filter by user ID
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of knowledge bases, total count)
        """
        search_pattern = f"%{search_term}%"
        stmt = select(Knowledge).where(
            or_(
                Knowledge.name.ilike(search_pattern),
                Knowledge.description.ilike(search_pattern),
            )
        )
        count_stmt = select(func.count(Knowledge.id)).where(
            or_(
                Knowledge.name.ilike(search_pattern),
                Knowledge.description.ilike(search_pattern),
            )
        )

        if user_id:
            normalized_user_id = normalize_uuid_to_str(user_id)
            stmt = stmt.where(Knowledge.user_id == normalized_user_id)
            count_stmt = count_stmt.where(Knowledge.user_id == normalized_user_id)

        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        stmt = stmt.order_by(Knowledge.updated_at.desc()).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def increment_document_count(
        self,
        knowledge_id: str | UUID,
        increment: int = 1,
        auto_commit: bool = False,
    ) -> Knowledge | None:
        """
        Increment the document count for a knowledge base.

        Args:
            knowledge_id: The knowledge base ID
            increment: Number to add to document count
            auto_commit: If True, immediately commit the transaction.

        Returns:
            Updated Knowledge instance or None if not found
        """
        knowledge = await self.get_by_id(knowledge_id)
        if not knowledge:
            return None

        knowledge.document_count += increment
        knowledge.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(knowledge)
        return knowledge

    async def increment_chunk_count(
        self,
        knowledge_id: str | UUID,
        increment: int = 1,
        auto_commit: bool = False,
    ) -> Knowledge | None:
        """
        Increment the chunk count for a knowledge base.

        Args:
            knowledge_id: The knowledge base ID
            increment: Number to add to chunk count
            auto_commit: If True, immediately commit the transaction.

        Returns:
            Updated Knowledge instance or None if not found
        """
        knowledge = await self.get_by_id(knowledge_id)
        if not knowledge:
            return None

        knowledge.chunk_count += increment
        knowledge.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(knowledge)
        return knowledge
