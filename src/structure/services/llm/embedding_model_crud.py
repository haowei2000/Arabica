"""CRUD operations for EmbeddingModel."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from structure.models.llm.embedding_model import EmbeddingModel
from structure.schemas.llm.embedding_model import (
    EmbeddingModelCreate,
    EmbeddingModelUpdate,
)


class EmbeddingModelCRUD:
    """CRUD operations for EmbeddingModel."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        data: EmbeddingModelCreate,
        user_id: str | UUID,
        auto_commit: bool = True,
    ) -> EmbeddingModel:
        """Create a new embedding model configuration."""
        create_data = data.model_dump()
        if create_data.get("is_default") is True:
            await self._clear_default_models()
        obj = EmbeddingModel(**create_data, user_id=str(user_id))
        self.db.add(obj)
        if auto_commit:
            await self.db.commit()
            await self.db.refresh(obj)
        else:
            await self.db.flush()
        return obj

    async def get_by_id(self, model_id: str) -> EmbeddingModel | None:
        result = await self.db.execute(
            select(EmbeddingModel).where(EmbeddingModel.id == model_id)
        )
        return result.scalar_one_or_none()

    async def update(
        self,
        model_id: str,
        data: EmbeddingModelUpdate,
        auto_commit: bool = True,
    ) -> EmbeddingModel | None:
        obj = await self.get_by_id(model_id)
        if not obj:
            return None
        update_data = data.model_dump(exclude_unset=True)
        if update_data.get("is_default") is True:
            await self._clear_default_models(exclude_id=model_id)
        for field, value in update_data.items():
            setattr(obj, field, value)
        if auto_commit:
            await self.db.commit()
            await self.db.refresh(obj)
        else:
            await self.db.flush()
        return obj

    async def delete(self, model_id: str, auto_commit: bool = True) -> bool:
        obj = await self.get_by_id(model_id)
        if not obj:
            return False
        await self.db.delete(obj)
        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()
        return True

    async def list(
        self,
        user_id: str | None = None,
        provider: str | None = None,
        enabled: bool | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[EmbeddingModel], int]:
        """Return paginated list of embedding models."""
        query = select(EmbeddingModel)
        if user_id is not None:
            query = query.where(
                or_(
                    EmbeddingModel.user_id == user_id,
                    EmbeddingModel.is_system.is_(True),
                )
            )
        if provider is not None:
            query = query.where(EmbeddingModel.provider == provider)
        if enabled is not None:
            query = query.where(EmbeddingModel.enabled == enabled)

        count_query = select(func.count()).select_from(query.subquery())
        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        query = (
            query.order_by(EmbeddingModel.created_at.desc()).offset(skip).limit(limit)
        )
        result = await self.db.execute(query)
        items = list(result.scalars().all())
        return items, total

    async def get_default(self) -> EmbeddingModel | None:
        """Return the enabled default embedding model, or None if not set."""
        result = await self.db.execute(
            select(EmbeddingModel)
            .where(
                EmbeddingModel.is_default.is_(True), EmbeddingModel.enabled.is_(True)
            )
            .order_by(EmbeddingModel.is_system.desc(), EmbeddingModel.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _clear_default_models(self, exclude_id: str | UUID | None = None) -> None:
        stmt = update(EmbeddingModel).where(EmbeddingModel.is_default.is_(True))
        if exclude_id is not None:
            stmt = stmt.where(EmbeddingModel.id != exclude_id)
        await self.db.execute(stmt.values(is_default=False))

    async def search(
        self,
        search_term: str,
        user_id: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[EmbeddingModel], int]:
        """Search embedding models by name or description."""
        pattern = f"%{search_term}%"
        query = select(EmbeddingModel).where(
            or_(
                EmbeddingModel.name.ilike(pattern),
                EmbeddingModel.description.ilike(pattern),
                EmbeddingModel.model_id.ilike(pattern),
            )
        )
        if user_id is not None:
            query = query.where(
                or_(
                    EmbeddingModel.user_id == user_id,
                    EmbeddingModel.is_system.is_(True),
                )
            )
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        query = (
            query.order_by(EmbeddingModel.created_at.desc()).offset(skip).limit(limit)
        )
        result = await self.db.execute(query)
        items = list(result.scalars().all())
        return items, total
