"""CRUD operations for Context model with vector search and text retrieval."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import Integer, and_, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.context.context import Context
from aiwen.schemas.agents.app import ContextType
from aiwen.schemas.context.context import ContextCreate, ContextUpdate


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


class ContextCRUD:
    """CRUD operations for Context model with retrieval methods."""

    def __init__(self, db_session: AsyncSession):
        """
        Initialize ContextCRUD with database session.

        Args:
            db_session: SQLAlchemy async session
        """
        self.db = db_session

    # ==================== Basic CRUD Operations ====================

    async def create(
        self,
        data: ContextCreate,
        user_id: str | UUID,
        auto_commit: bool = False,
    ) -> Context:
        """
        Create a new context entry.

        Args:
            data: Context creation data
            user_id: ID of the user creating the context
            auto_commit: If True, immediately commit the transaction.

        Returns:
            Created Context instance
        """
        data_dict = data.model_dump()
        data_dict["user_id"] = normalize_uuid_to_str(user_id)

        if data.source_id:
            data_dict["source_id"] = normalize_uuid_to_str(data.source_id)

        context = Context(**data_dict)
        self.db.add(context)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(context)
        return context

    async def update(
        self,
        context_id: str | UUID,
        user_id: str | UUID,
        data: ContextUpdate,
        auto_commit: bool = False,
    ) -> Context | None:
        """
        Update an existing context (with user_id validation).

        Args:
            context_id: The context ID to update
            user_id: The user ID (must match context owner)
            data: Update data
            auto_commit: If True, immediately commit the transaction.

        Returns:
            Updated Context instance or None if not found or unauthorized
        """
        context = await self.get_by_id(context_id, user_id)
        if not context:
            return None

        update_data = data.model_dump(exclude_unset=True)

        if update_data.get("source_id"):
            update_data["source_id"] = normalize_uuid_to_str(update_data["source_id"])

        for key, value in update_data.items():
            setattr(context, key, value)

        context.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(context)
        return context

    async def delete(
        self,
        context_id: str | UUID,
        user_id: str | UUID,
        auto_commit: bool = False,
    ) -> bool:
        """
        Delete a context entry (with user_id validation).

        Args:
            context_id: The context ID to delete
            user_id: The user ID (must match context owner)
            auto_commit: If True, immediately commit the transaction.

        Returns:
            True if deleted, False if not found or unauthorized
        """
        context = await self.get_by_id(context_id, user_id)
        if not context:
            return False

        await self.db.delete(context)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        return True

    # ==================== Select by ID Methods ====================

    async def get_by_id(
        self,
        context_id: str | UUID,
        user_id: str | UUID,
    ) -> Context | None:
        """
        Get context by ID with user_id validation.

        Args:
            context_id: The context ID to search for
            user_id: The user ID (must match context owner)

        Returns:
            Context instance or None if not found or unauthorized
        """
        normalized_id = normalize_uuid_to_str(context_id)
        normalized_user_id = normalize_uuid_to_str(user_id)

        stmt = select(Context).where(
            and_(
                Context.id == normalized_id,
                Context.user_id == normalized_user_id,
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_ids(
        self,
        context_ids: list[str | UUID],
        user_id: str | UUID,
    ) -> list[Context]:
        """
        Get multiple contexts by IDs with user_id validation.

        Args:
            context_ids: List of context IDs to fetch
            user_id: The user ID (must match context owner)

        Returns:
            List of Context instances (only those belonging to user)
        """
        if not context_ids:
            return []

        normalized_ids = [normalize_uuid_to_str(cid) for cid in context_ids]
        normalized_user_id = normalize_uuid_to_str(user_id)

        stmt = select(Context).where(
            and_(
                Context.id.in_(normalized_ids),
                Context.user_id == normalized_user_id,
            )
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ==================== List Methods ====================

    async def list(
        self,
        user_id: str | UUID,
        context_type: str | None = None,
        source_id: str | UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Context], int]:
        """
        List contexts with filtering and pagination (requires user_id).

        Args:
            user_id: Filter by user ID (required)
            context_type: Filter by context type
            source_id: Filter by source ID
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of contexts, total count)
        """
        normalized_user_id = normalize_uuid_to_str(user_id)

        conditions = [Context.user_id == normalized_user_id]

        if context_type:
            conditions.append(Context.context_type == context_type)

        if source_id:
            normalized_source_id = normalize_uuid_to_str(source_id)
            conditions.append(Context.source_id == normalized_source_id)

        # Count query
        count_stmt = select(func.count(Context.id)).where(and_(*conditions))
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Data query
        stmt = (
            select(Context)
            .where(and_(*conditions))
            .order_by(Context.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def get_by_user_and_type(
        self,
        user_id: str | UUID,
        context_type: str,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Context], int]:
        """
        Get contexts by user ID and context type.

        Args:
            user_id: The user ID
            context_type: The context type
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of contexts, total count)
        """
        return await self.list(
            user_id=user_id,
            context_type=context_type,
            skip=skip,
            limit=limit,
        )

    # ==================== Grep (Text Search) Methods ====================

    async def grep(
        self,
        query: str,
        user_id: str | UUID,
        context_type: str | None = None,
        source_id: str | UUID | None = None,
        search_in: list[str] | None = None,
        case_sensitive: bool = False,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[Context], int]:
        """
        Grep/text search in contexts (requires user_id).

        Args:
            query: Search query string
            user_id: Filter by user ID (required)
            context_type: Filter by context type
            source_id: Filter by source ID
            search_in: Fields to search in (content, summary, keywords)
            case_sensitive: Whether to use case sensitive search
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of matching contexts, total count)
        """
        normalized_user_id = normalize_uuid_to_str(user_id)

        if search_in is None:
            search_in = ["content", "summary"]

        # Base conditions
        conditions = [Context.user_id == normalized_user_id]

        if context_type:
            conditions.append(Context.context_type == context_type)

        if source_id:
            normalized_source_id = normalize_uuid_to_str(source_id)
            conditions.append(Context.source_id == normalized_source_id)

        # Build search conditions based on case sensitivity
        search_conditions = []
        search_pattern = f"%{query}%"

        if case_sensitive:
            if "content" in search_in:
                search_conditions.append(Context.content.like(search_pattern))
            if "summary" in search_in:
                search_conditions.append(Context.summary.like(search_pattern))
            if "keywords" in search_in:
                # Search in JSONB array using PostgreSQL contains
                search_conditions.append(
                    Context.keywords.cast(text("text")).like(search_pattern)
                )
        else:
            if "content" in search_in:
                search_conditions.append(Context.content.ilike(search_pattern))
            if "summary" in search_in:
                search_conditions.append(Context.summary.ilike(search_pattern))
            if "keywords" in search_in:
                # Case insensitive search in JSONB array
                search_conditions.append(
                    func.lower(Context.keywords.cast(text("text"))).like(
                        f"%{query.lower()}%"
                    )
                )

        if search_conditions:
            conditions.append(or_(*search_conditions))

        # Count query
        count_stmt = select(func.count(Context.id)).where(and_(*conditions))
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Data query
        stmt = (
            select(Context)
            .where(and_(*conditions))
            .order_by(Context.updated_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def search(
        self,
        search_term: str,
        user_id: str | UUID,
        context_type: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Context], int]:
        """
        Simple text search in content and summary (backward compatible).

        Args:
            search_term: Term to search for
            user_id: Filter by user ID (required)
            context_type: Filter by context type
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of contexts, total count)
        """
        return await self.grep(
            query=search_term,
            user_id=user_id,
            context_type=context_type,
            search_in=["content", "summary"],
            case_sensitive=False,
            skip=skip,
            limit=limit,
        )

    # ==================== Cosine Similarity (Vector Search) Methods ====================

    async def cosine_search(
        self,
        embedding: list[float],
        user_id: str | UUID,
        dimension: Literal[384, 768, 1024, 1536] = 1536,
        context_type: str | None = None,
        source_id: str | UUID | None = None,
        top_k: int = 10,
        threshold: float | None = None,
    ) -> list[tuple[Context, float]]:
        """
        Vector similarity search using cosine distance (requires user_id).

        Args:
            embedding: Query embedding vector
            user_id: Filter by user ID (required)
            dimension: Embedding dimension to search (384, 768, 1024, 1536)
            context_type: Filter by context type
            source_id: Filter by source ID
            top_k: Number of results to return
            threshold: Minimum similarity threshold (0-1, where 1 is identical)

        Returns:
            List of tuples (Context, similarity_score) ordered by similarity
        """
        normalized_user_id = normalize_uuid_to_str(user_id)

        # Map dimension to column name
        embedding_column_map = {
            384: Context.embedding_384,
            768: Context.embedding_768,
            1024: Context.embedding_1024,
            1536: Context.embedding_1536,
        }

        embedding_column = embedding_column_map.get(dimension)
        if embedding_column is None:
            raise ValueError(
                f"Invalid dimension: {dimension}. Must be 384, 768, 1024, or 1536"
            )

        # Validate embedding dimension
        if len(embedding) != dimension:
            raise ValueError(
                f"Embedding dimension mismatch: expected {dimension}, got {len(embedding)}"
            )

        # Build conditions
        conditions = [
            Context.user_id == normalized_user_id,
            embedding_column.isnot(None),  # Only search contexts with embeddings
        ]

        if context_type:
            conditions.append(Context.context_type == context_type)

        if source_id:
            normalized_source_id = normalize_uuid_to_str(source_id)
            conditions.append(Context.source_id == normalized_source_id)

        # Calculate cosine distance (pgvector uses <=> for cosine distance)
        # Cosine distance = 1 - cosine_similarity
        # So we need to convert: similarity = 1 - distance
        embedding_str = f"[{','.join(map(str, embedding))}]"
        distance_expr = embedding_column.op("<=>")(
            cast(embedding_str, embedding_column.type)
        )

        # Build query with distance calculation
        stmt = (
            select(Context, (1 - distance_expr).label("similarity"))
            .where(and_(*conditions))
            .order_by(
                distance_expr
            )  # Order by distance (ascending = most similar first)
            .limit(top_k)
        )

        result = await self.db.execute(stmt)
        rows = result.all()

        # Filter by threshold if specified
        results = []
        for row in rows:
            context = row[0]
            similarity = float(row[1])
            if threshold is None or similarity >= threshold:
                results.append((context, similarity))

        return results

    async def vector_search(
        self,
        embedding: list[float],
        user_id: str | UUID,
        dimension: Literal[384, 768, 1024, 1536] = 1536,
        context_type: str | None = None,
        source_id: str | UUID | None = None,
        top_k: int = 10,
        threshold: float | None = None,
    ) -> list[tuple[Context, float]]:
        """
        Alias for cosine_search for backward compatibility.
        """
        return await self.cosine_search(
            embedding=embedding,
            user_id=user_id,
            dimension=dimension,
            context_type=context_type,
            source_id=source_id,
            top_k=top_k,
            threshold=threshold,
        )

    # ==================== Hybrid Search Methods ====================

    async def hybrid_search(
        self,
        query: str,
        embedding: list[float],
        user_id: str | UUID,
        dimension: Literal[384, 768, 1024, 1536] = 1536,
        context_type: str | None = None,
        source_id: str | UUID | None = None,
        top_k: int = 10,
        vector_weight: float = 0.7,
        text_weight: float = 0.3,
    ) -> list[tuple[Context, float]]:
        """
        Hybrid search combining vector similarity and text matching.

        Args:
            query: Text search query
            embedding: Query embedding vector
            user_id: Filter by user ID (required)
            dimension: Embedding dimension to search
            context_type: Filter by context type
            source_id: Filter by source ID
            top_k: Number of results to return
            vector_weight: Weight for vector similarity (0-1)
            text_weight: Weight for text matching (0-1)

        Returns:
            List of tuples (Context, combined_score) ordered by score
        """
        # Get vector search results
        vector_results = await self.cosine_search(
            embedding=embedding,
            user_id=user_id,
            dimension=dimension,
            context_type=context_type,
            source_id=source_id,
            top_k=top_k * 2,  # Get more candidates for hybrid ranking
        )

        # Get text search results
        text_results, _ = await self.grep(
            query=query,
            user_id=user_id,
            context_type=context_type,
            source_id=source_id,
            limit=top_k * 2,
        )

        # Build a map of context_id -> scores
        scores: dict[str, dict[str, float]] = {}

        # Add vector scores
        for context, vector_score in vector_results:
            context_id = str(context.id)
            if context_id not in scores:
                scores[context_id] = {"context": context, "vector": 0.0, "text": 0.0}
            scores[context_id]["vector"] = vector_score
            scores[context_id]["context"] = context

        # Add text scores (simple binary: 1.0 if found, 0.0 otherwise)
        for i, context in enumerate(text_results):
            context_id = str(context.id)
            # Text relevance score decreases by rank
            text_score = 1.0 - (i / len(text_results)) if text_results else 0.0
            if context_id not in scores:
                scores[context_id] = {"context": context, "vector": 0.0, "text": 0.0}
            scores[context_id]["text"] = text_score
            scores[context_id]["context"] = context

        # Calculate combined scores
        results = []
        for context_id, score_data in scores.items():
            combined_score = (
                score_data["vector"] * vector_weight + score_data["text"] * text_weight
            )
            results.append((score_data["context"], combined_score))

        # Sort by combined score and return top_k
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    # ==================== Batch Operations ====================

    async def create_batch(
        self,
        items: list[ContextCreate],
        user_id: str | UUID,
        auto_commit: bool = False,
    ) -> list[Context]:
        """
        Create multiple context entries in batch.

        Args:
            items: List of context creation data
            user_id: ID of the user creating the contexts
            auto_commit: If True, immediately commit the transaction.

        Returns:
            List of created Context instances
        """
        normalized_user_id = normalize_uuid_to_str(user_id)
        contexts = []

        for data in items:
            data_dict = data.model_dump()
            data_dict["user_id"] = normalized_user_id

            if data.source_id:
                data_dict["source_id"] = normalize_uuid_to_str(data.source_id)

            context = Context(**data_dict)
            self.db.add(context)
            contexts.append(context)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        for context in contexts:
            await self.db.refresh(context)

        return contexts

    async def delete_by_source_id(
        self,
        source_id: str | UUID,
        user_id: str | UUID,
        auto_commit: bool = False,
    ) -> int:
        """
        Delete all contexts by source_id (with user_id validation).

        Args:
            source_id: The source ID to delete contexts for
            user_id: The user ID (must match context owner)
            auto_commit: If True, immediately commit the transaction.

        Returns:
            Number of deleted contexts
        """
        normalized_source_id = normalize_uuid_to_str(source_id)
        normalized_user_id = normalize_uuid_to_str(user_id)

        # Get contexts to delete
        stmt = select(Context).where(
            and_(
                Context.source_id == normalized_source_id,
                Context.user_id == normalized_user_id,
            )
        )
        result = await self.db.execute(stmt)
        contexts = list(result.scalars().all())

        count = len(contexts)
        for context in contexts:
            await self.db.delete(context)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        return count

    async def list_by_document_id(
        self,
        document_id: str | UUID,
        user_id: str | UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Context], int]:
        """
        List contexts by document_id in meta field (for chunks).

        Args:
            document_id: The document ID to filter by (from meta.document_id)
            user_id: Filter by user ID (required)
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of contexts, total count)
        """
        normalized_user_id = normalize_uuid_to_str(user_id)
        normalized_document_id = normalize_uuid_to_str(document_id)

        # Filter by meta->>'document_id' using PostgreSQL JSONB operators
        conditions = [
            Context.user_id == normalized_user_id,
            Context.context_type == ContextType.CHUNK.value,
            Context.meta.op("->>")("document_id") == normalized_document_id,
        ]

        # Count query
        count_stmt = select(func.count(Context.id)).where(and_(*conditions))
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Data query - order by position from meta JSONB field
        stmt = (
            select(Context)
            .where(and_(*conditions))
            .order_by(cast(Context.meta.op("->>")("position"), Integer))
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        items = list(result.scalars().all())

        return items, total
