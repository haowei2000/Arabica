"""CRUD operations for ContextSchema model with vector search and text retrieval."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Integer, and_, cast, delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.enums import ContextType
from structure.models.context.context import Context
from structure.schemas.context.context_schema import ContextCreate, ContextUpdate
from structure.utils.context import (
    context_path_variants,
    semantic_context_path,
)


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
    """CRUD operations for ContextSchema model with retrieval methods."""

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
            data: ContextSchema creation data
            user_id: ID of the user creating the context
            auto_commit: If True, immediately commit the transaction.

        Returns:
            Created ContextSchema instance
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
            Updated ContextSchema instance or None if not found or unauthorized
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
            ContextSchema instance or None if not found or unauthorized
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
            List of ContextSchema instances (only those belonging to user)
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
        knowledge_id: str | UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Context], int]:
        """
        List contexts with filtering and pagination (requires user_id).

        Args:
            user_id: Filter by user ID (required)
            context_type: Filter by context type
            source_id: Filter by source ID (direct column)
            knowledge_id: Filter by knowledge ID in metadata
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

        if knowledge_id:
            normalized_knowledge_id = normalize_uuid_to_str(knowledge_id)
            conditions.append(
                or_(
                    Context.source_id == normalized_knowledge_id,
                    Context.meta.op("->>")("knowledge_id") == normalized_knowledge_id,
                )
            )

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
        knowledge_id: str | UUID | None = None,
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
            knowledge_id: Filter by knowledge ID in metadata
            search_in: Fields to search in (content, summary, keywords)
            case_sensitive: Whether to use case sensitive search
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of matching contexts, total count)
        """
        normalized_user_id = normalize_uuid_to_str(user_id)

        if search_in is None:
            search_in = ["content", "glance"]

        # Base conditions
        conditions = [Context.user_id == normalized_user_id]

        if context_type:
            conditions.append(Context.context_type == context_type)

        if source_id:
            normalized_source_id = normalize_uuid_to_str(source_id)
            conditions.append(Context.source_id == normalized_source_id)

        if knowledge_id:
            normalized_knowledge_id = normalize_uuid_to_str(knowledge_id)
            conditions.append(
                or_(
                    Context.source_id == normalized_knowledge_id,
                    Context.meta.op("->>")("knowledge_id") == normalized_knowledge_id,
                )
            )

        # Build search conditions: phrase match OR per-token AND match (for multi-word queries)
        def _field_ilike(field: str, pattern: str) -> Any:
            col = Context.content if field == "content" else Context.glance
            return col.like(pattern) if case_sensitive else col.ilike(pattern)

        search_pattern = f"%{query}%"
        phrase_conds = [_field_ilike(f, search_pattern) for f in search_in]

        # For multi-word queries, also match docs where every significant token appears
        tokens = [t for t in query.split() if len(t) >= 3]
        token_conds: list[Any] = []
        if len(tokens) > 1:
            for token in tokens:
                tp = f"%{token}%"
                per_token = [_field_ilike(f, tp) for f in search_in]
                if per_token:
                    token_conds.append(or_(*per_token))

        search_conditions = phrase_conds
        if token_conds:
            search_conditions = phrase_conds + [and_(*token_conds)]  # noqa: RUF005

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
        knowledge_id: str | UUID | None = None,
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
            knowledge_id: Filter by knowledge ID in metadata
            top_k: Number of results to return
            threshold: Minimum similarity threshold (0-1, where 1 is identical)

        Returns:
            List of tuples (ContextSchema, similarity_score) ordered by similarity
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

        if knowledge_id:
            normalized_knowledge_id = normalize_uuid_to_str(knowledge_id)
            conditions.append(
                or_(
                    Context.source_id == normalized_knowledge_id,
                    Context.meta.op("->>")("knowledge_id") == normalized_knowledge_id,
                )
            )

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
        knowledge_id: str | UUID | None = None,
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
            knowledge_id=knowledge_id,
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
        knowledge_id: str | UUID | None = None,
        top_k: int = 10,
        vector_weight: float = 0.3,  # Reduced default
        text_weight: float = 0.7,  # Increased default for keyword priority
    ) -> list[tuple[Context, float]]:
        """
        Hybrid search combining vector similarity and text matching.
        Prioritizes keyword matches (text) over semantic similarity.

        Args:
            query: Text search query
            embedding: Query embedding vector
            user_id: Filter by user ID (required)
            dimension: Embedding dimension to search
            context_type: Filter by context type
            source_id: Filter by source ID
            knowledge_id: Filter by knowledge ID in metadata
            top_k: Number of results to return
            vector_weight: Weight for vector similarity (0-1)
            text_weight: Weight for text matching (0-1)

        Returns:
            List of tuples (Context, combined_score) ordered by score
        """
        # 1. Get vector search results (semantic)
        vector_results = await self.cosine_search(
            embedding=embedding,
            user_id=user_id,
            dimension=dimension,
            context_type=context_type,
            source_id=source_id,
            knowledge_id=knowledge_id,
            top_k=top_k * 5,  # Larger candidate pool for better recall
        )

        # 2. Get text search results (keyword + token matching, incl. glance field)
        text_results, _ = await self.grep(
            query=query,
            user_id=user_id,
            context_type=context_type,
            source_id=source_id,
            knowledge_id=knowledge_id,
            limit=top_k * 5,
        )

        # 3. Score Merge with Keyword Priority
        scores: dict[str, dict[str, Any]] = {}

        # Add vector scores (0.0 to 1.0)
        for context, vector_score in vector_results:
            context_id = str(context.id)
            scores[context_id] = {
                "context": context,
                "vector": vector_score,
                "text": 0.0,
                "bonus": 0.0,
            }

        # Add text scores and bonuses
        query_lower = query.lower()
        for i, context in enumerate(text_results):
            context_id = str(context.id)
            # Text relevance score decreases by rank (1.0 to 0.1)
            text_score = (
                max(0.1, 1.0 - (i / len(text_results))) if text_results else 0.0
            )

            if context_id not in scores:
                scores[context_id] = {
                    "context": context,
                    "vector": 0.0,
                    "text": text_score,
                    "bonus": 0.0,
                }
            else:
                scores[context_id]["text"] = text_score
                # Keyword + Semantic intersection bonus
                scores[context_id]["bonus"] += 0.1

            # Exact phrase match bonus in content or glance
            content_lower = context.content.lower()
            glance_lower = (context.glance or "").lower()
            if query_lower in glance_lower:
                scores[context_id]["bonus"] += 0.3
            elif query_lower in content_lower:
                scores[context_id]["bonus"] += 0.15

        # 4. Calculate combined scores
        final_results = []
        for context_id, data in scores.items():  # noqa: B007
            # Formula: (Vector * W_v) + (Text * W_t) + Bonus  # noqa: ERA001
            # Bonus can push a score above 1.0, ensuring top ranking for exact keyword matches
            combined_score = (
                data["vector"] * vector_weight
                + data["text"] * text_weight
                + data["bonus"]
            )
            final_results.append((data["context"], combined_score))

        # Sort by combined score descending
        final_results.sort(key=lambda x: x[1], reverse=True)
        return final_results[:top_k]

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
            List of created ContextSchema instances
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

        # Count first, then bulk delete in a single statement
        count_stmt = select(func.count(Context.id)).where(
            and_(
                Context.source_id == normalized_source_id,
                Context.user_id == normalized_user_id,
            )
        )
        count_result = await self.db.execute(count_stmt)
        count = count_result.scalar() or 0

        if count:
            del_stmt = delete(Context).where(
                and_(
                    Context.source_id == normalized_source_id,
                    Context.user_id == normalized_user_id,
                )
            )
            await self.db.execute(del_stmt)

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

    # ==================== Upsert Methods ====================

    async def upsert_by_path(
        self,
        user_id: str | UUID,
        path: str,
        data: dict[str, Any],
        auto_commit: bool = False,
    ) -> Context:
        """Upsert a Context row keyed on (user_id, path).

        Creates the row if it doesn't exist; updates glance/content/tags/meta
        and clears embeddings when content changes.
        """
        normalized_user_id = normalize_uuid_to_str(user_id)
        normalized_path = semantic_context_path(path) or "/"
        path_variants = context_path_variants(normalized_path)

        stmt = select(Context).where(
            Context.user_id == normalized_user_id,
            Context.path.in_(path_variants),
        )
        result = await self.db.execute(stmt)
        ctx = result.scalars().first()

        content = data.get("content", "")
        if ctx:
            content_changed = content != ctx.content
            ctx.glance = data.get("glance", ctx.glance)
            ctx.path = normalized_path
            ctx.content = content
            if data.get("tags") is not None:
                ctx.tags = data["tags"]
            if data.get("meta") is not None:
                ctx.meta = {**(ctx.meta or {}), **data["meta"]}
            if data.get("source_id") is not None:
                ctx.source_id = UUID(str(data["source_id"]))
            if content_changed:
                ctx.embedding_384 = None
                ctx.embedding_768 = None
                ctx.embedding_1024 = None
                ctx.embedding_1536 = None
        else:
            source_id = data.get("source_id")
            ctx = Context(
                id=uuid4(),
                user_id=normalized_user_id,
                path=normalized_path,
                context_type=data.get("context_type", ContextType.WORKSPACE),
                source_id=UUID(str(source_id)) if source_id else None,
                glance=data.get("glance", ""),
                content=content,
                tags=data.get("tags", []),
                meta=data.get("meta", {}),
            )
            self.db.add(ctx)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        return ctx

    async def upsert_by_source(
        self,
        source_id: str | UUID,
        context_type: str,
        user_id: str | UUID,
        data: dict[str, Any],
        auto_commit: bool = False,
    ) -> tuple[Context, bool]:
        """Upsert a Context row keyed on (source_id, context_type, user_id).

        Returns ``(ctx, needs_embedding)`` — True when content changed or is new.
        """
        normalized_user_id = normalize_uuid_to_str(user_id)
        source_uuid = UUID(str(source_id))
        normalized_path = semantic_context_path(data.get("path"))

        stmt = select(Context).where(
            Context.source_id == source_uuid,
            Context.context_type == context_type,
            Context.user_id == normalized_user_id,
        )
        result = await self.db.execute(stmt)
        ctx = result.scalars().first()

        content = data.get("content") or ""
        if ctx:
            new_content = content or ctx.content
            content_changed = new_content != ctx.content
            ctx.glance = data.get("glance", ctx.glance)
            ctx.content = new_content
            if normalized_path is not None:
                ctx.path = normalized_path
            if data.get("tags") is not None:
                ctx.tags = data["tags"]
            ctx.meta = {**(ctx.meta or {}), **(data.get("meta") or {})}
            if content_changed:
                ctx.embedding_384 = None
                ctx.embedding_768 = None
                ctx.embedding_1024 = None
                ctx.embedding_1536 = None
            needs_embedding = content_changed
        else:
            ctx = Context(
                id=uuid4(),
                user_id=normalized_user_id,
                context_type=context_type,
                source_id=source_uuid,
                glance=data.get("glance"),
                path=normalized_path,
                content=content,
                tags=data.get("tags") or [],
                meta=data.get("meta") or {},
            )
            self.db.add(ctx)
            needs_embedding = True

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        return ctx, needs_embedding

    async def upsert_by_source_and_path(
        self,
        source_id: str | UUID,
        context_type: str,
        user_id: str | UUID,
        path: str,
        data: dict[str, Any],
        auto_commit: bool = False,
    ) -> tuple[Context, bool]:
        """Upsert a Context row keyed on (source_id, context_type, user_id, path).

        Multiple entries with different paths can coexist for the same source
        (e.g. per-section chunks of a skill or knowledge base).

        Returns ``(ctx, needs_embedding)``.
        """
        normalized_user_id = normalize_uuid_to_str(user_id)
        source_uuid = UUID(str(source_id))
        normalized_path = semantic_context_path(path) or "/"
        path_variants = context_path_variants(normalized_path)

        stmt = select(Context).where(
            Context.source_id == source_uuid,
            Context.context_type == context_type,
            Context.user_id == normalized_user_id,
            Context.path.in_(path_variants),
        )
        result = await self.db.execute(stmt)
        ctx = result.scalars().first()

        content = data.get("content") or ""
        if ctx:
            new_content = content or ctx.content
            content_changed = new_content != ctx.content
            ctx.glance = data.get("glance", ctx.glance)
            ctx.path = normalized_path
            ctx.content = new_content
            if data.get("tags") is not None:
                ctx.tags = data["tags"]
            ctx.meta = {**(ctx.meta or {}), **(data.get("meta") or {})}
            if content_changed:
                ctx.embedding_384 = None
                ctx.embedding_768 = None
                ctx.embedding_1024 = None
                ctx.embedding_1536 = None
            needs_embedding = content_changed
        else:
            ctx = Context(
                id=uuid4(),
                user_id=normalized_user_id,
                context_type=context_type,
                source_id=source_uuid,
                glance=data.get("glance"),
                path=normalized_path,
                content=content,
                tags=data.get("tags") or [],
                meta=data.get("meta") or {},
            )
            self.db.add(ctx)
            needs_embedding = True

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        return ctx, needs_embedding

    async def create_batch_raw(
        self,
        rows: list[dict[str, Any]],
        auto_commit: bool = False,
    ) -> list[Context]:
        """Bulk-insert pre-built Context rows from raw dicts.

        Intended for high-volume knowledge chunk insertion where embeddings
        are already computed. Each dict must contain all required Context fields.
        """
        contexts = []
        for row in rows:
            if "id" not in row:
                row = {**row, "id": uuid4()}
            ctx = Context(**row)
            self.db.add(ctx)
            contexts.append(ctx)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        for ctx in contexts:
            await self.db.refresh(ctx)

        return contexts
