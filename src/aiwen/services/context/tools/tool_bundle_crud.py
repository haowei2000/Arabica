"""CRUD operations for ToolBundle."""

from __future__ import annotations

import logging
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aiwen.models.context.tools import Tool, ToolBundle, ToolBundleItem
from aiwen.schemas.context.tools.tool_bundle import ToolBundleCreate, ToolBundleUpdate

logger = logging.getLogger(__name__)


class ToolBundleCRUD:
    """CRUD operations for tool bundles."""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def _ensure_tools_visible(self, user_id: UUID, tool_ids: Iterable[UUID]) -> list[UUID]:
        ids = [UUID(str(tid)) for tid in tool_ids]
        if not ids:
            return []

        stmt = (
            select(Tool.id)
            .where(Tool.id.in_(ids))
            .where(
                (Tool.user_id == user_id)
                | (Tool.is_public == True)  # noqa: E712
                | (Tool.tool_type == "inner")
            )
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        visible = set(rows)
        missing = [tid for tid in ids if tid not in visible]
        if missing:
            raise ValueError(
                "Some tools are not accessible: " + ", ".join(str(t) for t in missing)
            )
        return ids

    async def create(
        self,
        user_id: UUID,
        data: ToolBundleCreate,
        auto_commit: bool = True,
    ) -> ToolBundle:
        # Check name uniqueness per user
        exists_stmt = select(ToolBundle.id).where(
            ToolBundle.user_id == user_id,
            ToolBundle.name == data.name,
        )
        existing = (await self.db.execute(exists_stmt)).scalar_one_or_none()
        if existing:
            raise ValueError(f"Tool bundle name '{data.name}' already exists")

        tool_ids = await self._ensure_tools_visible(user_id, data.tool_ids)

        bundle = ToolBundle(
            user_id=user_id,
            name=data.name,
            description=data.description,
            tags=data.tags,
            is_public=data.is_public,
        )
        self.db.add(bundle)
        await self.db.flush()

        for idx, tool_id in enumerate(tool_ids):
            self.db.add(ToolBundleItem(bundle_id=bundle.id, tool_id=tool_id, position=idx))

        if auto_commit:
            await self.db.commit()
            await self.db.refresh(bundle)

        logger.info("Created tool bundle %s (id=%s)", bundle.name, bundle.id)
        return bundle

    async def get_by_id(
        self, bundle_id: UUID, user_id: UUID | None = None
    ) -> ToolBundle | None:
        stmt = (
            select(ToolBundle)
            .options(selectinload(ToolBundle.items))
            .where(ToolBundle.id == bundle_id)
        )
        if user_id:
            stmt = stmt.where(
                (ToolBundle.user_id == user_id)
                | (ToolBundle.is_public == True)  # noqa: E712
            )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        user_id: UUID,
        include_public: bool = True,
        tags: list[str] | None = None,
    ) -> list[ToolBundle]:
        stmt = select(ToolBundle).options(selectinload(ToolBundle.items))
        if include_public:
            stmt = stmt.where(
                (ToolBundle.user_id == user_id)
                | (ToolBundle.is_public == True)  # noqa: E712
            )
        else:
            stmt = stmt.where(ToolBundle.user_id == user_id)

        if tags:
            for tag in tags:
                stmt = stmt.where(ToolBundle.tags.contains([tag]))

        stmt = stmt.order_by(ToolBundle.updated_at.desc().nulls_last(), ToolBundle.created_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update(
        self,
        bundle_id: UUID,
        user_id: UUID,
        data: ToolBundleUpdate,
        auto_commit: bool = True,
    ) -> ToolBundle | None:
        bundle = await self.get_by_id(bundle_id, user_id)
        if not bundle or bundle.user_id != user_id:
            return None

        update_data = data.model_dump(exclude_unset=True)

        if "name" in update_data:
            exists_stmt = select(ToolBundle.id).where(
                ToolBundle.user_id == user_id,
                ToolBundle.name == update_data["name"],
                ToolBundle.id != bundle_id,
            )
            existing = (await self.db.execute(exists_stmt)).scalar_one_or_none()
            if existing:
                raise ValueError(f"Tool bundle name '{update_data['name']}' already exists")
            bundle.name = update_data["name"]

        if "description" in update_data:
            bundle.description = update_data["description"]
        if "tags" in update_data:
            bundle.tags = update_data["tags"]
        if "is_public" in update_data:
            bundle.is_public = update_data["is_public"]

        if "tool_ids" in update_data and update_data["tool_ids"] is not None:
            tool_ids = await self._ensure_tools_visible(user_id, update_data["tool_ids"])
            # Replace items
            bundle.items.clear()
            await self.db.flush()
            for idx, tool_id in enumerate(tool_ids):
                bundle.items.append(
                    ToolBundleItem(bundle_id=bundle.id, tool_id=tool_id, position=idx)
                )

        if auto_commit:
            await self.db.commit()
            await self.db.refresh(bundle)

        logger.info("Updated tool bundle %s (id=%s)", bundle.name, bundle.id)
        return bundle

    async def delete(self, bundle_id: UUID, user_id: UUID, auto_commit: bool = True) -> bool:
        bundle = await self.get_by_id(bundle_id, user_id)
        if not bundle or bundle.user_id != user_id:
            return False

        await self.db.delete(bundle)
        if auto_commit:
            await self.db.commit()

        logger.info("Deleted tool bundle (id=%s)", bundle_id)
        return True
