"""CRUD operations for Document model."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.agents.docments import Document


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


class DocumentCRUD:
    """CRUD operations for Document model."""

    def __init__(self, db_session: AsyncSession):
        """
        Initialize DocumentCRUD with database session.

        Args:
            db_session: SQLAlchemy async session
        """
        self.db = db_session

    async def create(
            self,
            knowledge_id: str | UUID,
            user_id: str | UUID,
            original_name: str,
            object_key: str,
            file_size: int,
            file_hash: str | None = None,
            file_url: str | None = None,
            mime_type: str | None = None,
            storage_type: str = "s3",
            bucket_name: str | None = None,
            auto_commit: bool = False,
    ) -> Document:
        """
        Create a new document.

        Args:
            knowledge_id: ID of the knowledge base
            user_id: ID of the user uploading the document
            original_name: Original file name
            object_key: S3 object key
            file_size: File size in bytes
            file_hash: SHA256 hash for deduplication
            file_url: Access URL
            mime_type: MIME type
            storage_type: Storage type (s3/oss/local)
            bucket_name: Storage bucket name
            auto_commit: If True, immediately commit the transaction.

        Returns:
            Created Document instance
        """
        document = Document(
            knowledge_id=normalize_uuid_to_str(knowledge_id),
            user_id=normalize_uuid_to_str(user_id),
            original_name=original_name,
            object_key=object_key,
            file_size=file_size,
            file_hash=file_hash,
            file_url=file_url,
            mime_type=mime_type,
            storage_type=storage_type,
            bucket_name=bucket_name,
        )
        self.db.add(document)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(document)
        return document

    async def get_by_id(
            self,
            document_id: str | UUID,
    ) -> Document | None:
        """
        Get document by ID.

        Args:
            document_id: The document ID to search for

        Returns:
            Document instance or None if not found
        """
        normalized_id = normalize_uuid_to_str(document_id)
        stmt = select(Document).where(
            and_(
                Document.id == normalized_id,
                Document.is_deleted == False,  # noqa: E712
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_and_user(
            self,
            document_id: str | UUID,
            user_id: str | UUID,
    ) -> Document | None:
        """
        Get document by ID with user validation.

        Args:
            document_id: The document ID to search for
            user_id: The user ID (must match document owner)

        Returns:
            Document instance or None if not found or unauthorized
        """
        normalized_id = normalize_uuid_to_str(document_id)
        normalized_user_id = normalize_uuid_to_str(user_id)

        stmt = select(Document).where(
            and_(
                Document.id == normalized_id,
                Document.user_id == normalized_user_id,
                Document.is_deleted == False,  # noqa: E712
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_knowledge_id(
            self,
            knowledge_id: str | UUID,
            user_id: str | UUID,
            skip: int = 0,
            limit: int = 100,
    ) -> tuple[list[Document], int]:
        """
        Get documents by knowledge ID.

        Args:
            knowledge_id: The knowledge base ID
            user_id: The user ID (must match document owner)
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of documents, total count)
        """
        normalized_knowledge_id = normalize_uuid_to_str(knowledge_id)
        normalized_user_id = normalize_uuid_to_str(user_id)

        conditions = [
            Document.knowledge_id == normalized_knowledge_id,
            Document.user_id == normalized_user_id,
            Document.is_deleted == False,  # noqa: E712
        ]

        # Count query
        count_stmt = select(func.count(Document.id)).where(and_(*conditions))
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Data query
        stmt = (
            select(Document)
            .where(and_(*conditions))
            .order_by(Document.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def get_by_file_hash(
            self,
            file_hash: str,
    ) -> Document | None:
        """
        Get document by file hash for deduplication.

        Args:
            file_hash: SHA256 hash of the file

        Returns:
            Document instance or None if not found
        """
        stmt = select(Document).where(
            and_(
                Document.file_hash == file_hash,
                Document.is_deleted == False,  # noqa: E712
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def delete(
            self,
            document_id: str | UUID,
            user_id: str | UUID,
            auto_commit: bool = False,
    ) -> bool:
        """
        Soft delete a document (with user_id validation).

        Args:
            document_id: The document ID to delete
            user_id: The user ID (must match document owner)
            auto_commit: If True, immediately commit the transaction.

        Returns:
            True if deleted, False if not found or unauthorized
        """
        document = await self.get_by_id_and_user(document_id, user_id)
        if not document:
            return False

        document.is_deleted = True
        document.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        return True

    async def list(
            self,
            knowledge_id: str | UUID,
            user_id: str | UUID,
            skip: int = 0,
            limit: int = 100,
    ) -> tuple[list[Document], int]:
        """
        List documents with filtering and pagination.

        Args:
            knowledge_id: Filter by knowledge ID
            user_id: Filter by user ID
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of documents, total count)
        """
        return await self.get_by_knowledge_id(
            knowledge_id=knowledge_id,
            user_id=user_id,
            skip=skip,
            limit=limit,
        )

    async def increment_reference_count(
            self,
            document_id: str | UUID,
            increment: int = 1,
            auto_commit: bool = False,
    ) -> Document | None:
        """
        Increment the reference count for a document.

        Args:
            document_id: The document ID
            increment: Number to add to reference count
            auto_commit: If True, immediately commit the transaction.

        Returns:
            Updated Document instance or None if not found
        """
        document = await self.get_by_id(document_id)
        if not document:
            return None

        document.reference_count += increment
        document.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(document)
        return document
