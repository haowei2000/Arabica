"""CRUD operations for Skill (dedicated skill table)."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.context.skill import Skill
from aiwen.schemas.context.skill import SkillCreate, SkillUpdate
from aiwen.services.context.context_syncer import ContextSyncer

logger = logging.getLogger(__name__)


class SkillCRUD:
    """CRUD operations for the Skill table."""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def create(
        self,
        data: SkillCreate,
        user_id: str | UUID,
        auto_commit: bool = True,
    ) -> Skill:
        skill = Skill(
            user_id=UUID(str(user_id)),
            name=data.name,
            description=data.description,
            tags=data.tags or [],
            meta=data.meta or {},
        )

        self.db.add(skill)

        if auto_commit:
            await self.db.commit()
            await self.db.refresh(skill)
        else:
            await self.db.flush()
            await self.db.refresh(skill)

        await ContextSyncer(self.db).sync_skill(skill, content=data.content)

        logger.info(f"Created skill: {data.name} (id={skill.id}, user={user_id})")
        return skill

    async def get_by_id(
        self, skill_id: str | UUID, user_id: str | UUID | None = None
    ) -> Skill | None:
        query = select(Skill).where(Skill.id == UUID(str(skill_id)))
        if user_id:
            query = query.where(Skill.user_id == UUID(str(user_id)))
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str, user_id: str | UUID) -> Skill | None:
        result = await self.db.execute(
            select(Skill).where(
                Skill.user_id == UUID(str(user_id)),
                Skill.name == name,
            )
        )
        return result.scalar_one_or_none()

    async def update(
        self,
        skill_id: str | UUID,
        data: SkillUpdate,
        user_id: str | UUID,
        auto_commit: bool = True,
    ) -> Skill | None:
        skill = await self.get_by_id(skill_id, user_id)
        if not skill:
            return None

        update_data = data.model_dump(exclude_unset=True)

        if "name" in update_data:
            skill.name = update_data["name"]

        if "description" in update_data:
            skill.description = update_data["description"]

        if "tags" in update_data:
            skill.tags = update_data["tags"]

        if update_data.get("meta"):
            skill.meta = {**(skill.meta or {}), **update_data["meta"]}

        skill.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db.commit()
            await self.db.refresh(skill)
        else:
            await self.db.flush()
            await self.db.refresh(skill)

        await ContextSyncer(self.db).sync_skill(skill, content=update_data.get("content"))

        logger.info(f"Updated skill: {skill.id}")
        return skill

    async def delete(
        self,
        skill_id: str | UUID,
        user_id: str | UUID,
        auto_commit: bool = True,
    ) -> bool:
        skill = await self.get_by_id(skill_id, user_id)
        if not skill:
            return False

        await ContextSyncer(self.db).remove_skill(skill)
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
    ) -> tuple[list[Skill], int]:
        query = select(Skill).where(Skill.user_id == UUID(str(user_id)))

        if tags:
            for tag in tags:
                query = query.where(Skill.tags.contains([tag]))

        count_query = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_query)).scalar_one()

        query = query.order_by(Skill.updated_at.desc()).offset(skip).limit(limit)
        items = list((await self.db.execute(query)).scalars().all())

        return items, total

    async def search(
        self,
        user_id: str | UUID,
        query_text: str,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[Skill], int]:
        query = select(Skill).where(
            Skill.user_id == UUID(str(user_id)),
            or_(
                Skill.name.ilike(f"%{query_text}%"),
                Skill.description.ilike(f"%{query_text}%"),
            ),
        )

        count_query = select(func.count()).select_from(query.subquery())
        total = (await self.db.execute(count_query)).scalar_one()

        query = query.order_by(Skill.updated_at.desc()).offset(skip).limit(limit)
        items = list((await self.db.execute(query)).scalars().all())

        return items, total
