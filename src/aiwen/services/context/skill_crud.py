"""CRUD operations for Skill (Context with type=SKILL)."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.core.enums import ContextType
from aiwen.models.context.context import Context
from aiwen.schemas.context.skill import SkillCreate, SkillUpdate

logger = logging.getLogger(__name__)


def normalize_uuid_to_str(val: str | UUID) -> str:
    """Normalize a UUID value to string."""
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, str):
        try:
            UUID(val)
            return val
        except ValueError as e:
            raise ValueError(f"Invalid UUID string: {val}") from e
    raise TypeError(f"Expected UUID or str, got {type(val)}")


class SkillCRUD:
    """CRUD operations for Skills (Context entries with type=SKILL)."""

    def __init__(self, db_session: AsyncSession):
        """Initialize SkillCRUD with database session."""
        self.db = db_session

    async def create(
        self,
        data: SkillCreate,
        user_id: str | UUID,
        auto_commit: bool = True,
    ) -> Context:
        """
        Create a new skill.

        Args:
            data: Skill creation data
            user_id: ID of the user creating the skill
            auto_commit: If True, immediately commit the transaction

        Returns:
            Created Context instance (with type=SKILL)
        """
        # Generate glance from name
        glance = f"Skill: {data.name}"
        if data.description:
            glance = f"{data.name} — {data.description[:50]}"

        # Build metadata
        meta = data.meta or {}
        meta["name"] = data.name
        if data.description:
            meta["description"] = data.description

        skill = Context(
            user_id=normalize_uuid_to_str(user_id),
            source_id=data.source_id,
            path=data.path,
            context_type=ContextType.SKILL.value,
            glance=glance,
            summary=data.description,
            content=data.content,
            tags=data.tags or [],
            meta=meta,
        )

        self.db.add(skill)

        if auto_commit:
            await self.db.commit()
            await self.db.refresh(skill)
        else:
            await self.db.flush()
            await self.db.refresh(skill)

        logger.info(f"Created skill: {data.name} (id={skill.id}, user={user_id})")
        return skill

    async def get_by_id(self, skill_id: str | UUID, user_id: str | UUID | None = None) -> Context | None:
        """
        Get skill by ID.

        Args:
            skill_id: Skill ID
            user_id: Optional user ID for ownership check

        Returns:
            Context instance or None
        """
        normalized_id = normalize_uuid_to_str(skill_id)
        query = select(Context).where(
            Context.id == normalized_id,
            Context.context_type == ContextType.SKILL.value,
        )

        if user_id:
            query = query.where(Context.user_id == normalize_uuid_to_str(user_id))

        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str, user_id: str | UUID) -> Context | None:
        """
        Get skill by name for a specific user.

        Args:
            name: Skill name
            user_id: User ID

        Returns:
            Context instance or None
        """
        normalized_user_id = normalize_uuid_to_str(user_id)

        # Name is stored in meta.name
        query = select(Context).where(
            Context.user_id == normalized_user_id,
            Context.context_type == ContextType.SKILL.value,
            Context.meta["name"].astext == name,
        )

        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def update(
        self,
        skill_id: str | UUID,
        data: SkillUpdate,
        user_id: str | UUID,
        auto_commit: bool = True,
    ) -> Context | None:
        """
        Update an existing skill.

        Args:
            skill_id: Skill ID to update
            data: Update data
            user_id: User ID for ownership check
            auto_commit: If True, immediately commit the transaction

        Returns:
            Updated Context instance or None if not found
        """
        skill = await self.get_by_id(skill_id, user_id)
        if not skill:
            return None

        update_data = data.model_dump(exclude_unset=True)

        # Update meta with name/description if provided
        if "name" in update_data or "description" in update_data:
            meta = skill.meta or {}
            if "name" in update_data:
                meta["name"] = update_data["name"]
                # Update glance
                skill.glance = f"Skill: {update_data['name']}"
                if update_data.get("description") or skill.summary:
                    desc = update_data.get("description") or skill.summary
                    skill.glance = f"{update_data['name']} — {desc[:50]}"
            if "description" in update_data:
                meta["description"] = update_data["description"]
                skill.summary = update_data["description"]
            skill.meta = meta

        if "content" in update_data:
            skill.content = update_data["content"]
            # Clear embeddings when content changes
            skill.embedding_384 = None
            skill.embedding_768 = None
            skill.embedding_1024 = None
            skill.embedding_1536 = None

        if "tags" in update_data:
            skill.tags = update_data["tags"]

        skill.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
            await self.db.refresh(skill)
        else:
            await self.db.flush()
            await self.db.refresh(skill)

        logger.info(f"Updated skill: {skill.id}")
        return skill

    async def delete(
        self,
        skill_id: str | UUID,
        user_id: str | UUID,
        auto_commit: bool = True,
    ) -> bool:
        """
        Delete a skill (hard delete).

        Args:
            skill_id: Skill ID to delete
            user_id: User ID for ownership check
            auto_commit: If True, immediately commit the transaction

        Returns:
            True if deleted, False if not found
        """
        skill = await self.get_by_id(skill_id, user_id)
        if not skill:
            return False

        await self.db.delete(skill)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        logger.info(f"Deleted skill: {skill_id}")
        return True

    async def list(
        self,
        user_id: str | UUID,
        tags: list[str] | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[Context], int]:
        """
        List skills for a user with pagination.

        Args:
            user_id: User ID
            tags: Optional tag filter
            skip: Number of items to skip
            limit: Maximum number of items to return

        Returns:
            Tuple of (list of skills, total count)
        """
        normalized_user_id = normalize_uuid_to_str(user_id)

        query = select(Context).where(
            Context.user_id == normalized_user_id,
            Context.context_type == ContextType.SKILL.value,
        )

        # Filter by tags if provided
        if tags:
            for tag in tags:
                query = query.where(Context.tags.contains([tag]))

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        # Apply pagination and ordering
        query = query.order_by(Context.updated_at.desc()).offset(skip).limit(limit)

        result = await self.db.execute(query)
        items = list(result.scalars().all())

        return items, total

    async def search(
        self,
        user_id: str | UUID,
        query_text: str,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[Context], int]:
        """
        Search skills by name or content.

        Args:
            user_id: User ID
            query_text: Search query
            skip: Number of items to skip
            limit: Maximum number of items to return

        Returns:
            Tuple of (list of matching skills, total count)
        """
        normalized_user_id = normalize_uuid_to_str(user_id)

        query = select(Context).where(
            Context.user_id == normalized_user_id,
            Context.context_type == ContextType.SKILL.value,
            or_(
                Context.meta["name"].astext.ilike(f"%{query_text}%"),
                Context.content.ilike(f"%{query_text}%"),
                Context.summary.ilike(f"%{query_text}%"),
            ),
        )

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        # Apply pagination and ordering
        query = query.order_by(Context.updated_at.desc()).offset(skip).limit(limit)

        result = await self.db.execute(query)
        items = list(result.scalars().all())

        return items, total
