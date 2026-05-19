"""CRUD operations for ChatModel."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.constants.llm import MASKED_API_KEY_REF
from structure.models.llm.chat_model import ChatModel
from structure.schemas.llm.chat_model import ChatModelCreate, ChatModelUpdate


class ChatModelCRUD:
    """CRUD operations for ChatModel."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        data: ChatModelCreate,
        user_id: str | UUID,
        auto_commit: bool = True,
    ) -> ChatModel:
        """Create a new chat model configuration."""
        create_data = data.model_dump()
        if create_data.get("is_default") is True:
            await self._clear_default_models()
        obj = ChatModel(**create_data, user_id=str(user_id))
        self.db.add(obj)
        if auto_commit:
            await self.db.commit()
            await self.db.refresh(obj)
        else:
            await self.db.flush()
        return obj

    async def get_by_id(self, model_id: str) -> ChatModel | None:
        result = await self.db.execute(
            select(ChatModel).where(ChatModel.id == model_id)
        )
        return result.scalar_one_or_none()

    async def update(
        self,
        model_id: str,
        data: ChatModelUpdate,
        auto_commit: bool = True,
    ) -> ChatModel | None:
        obj = await self.get_by_id(model_id)
        if not obj:
            return None
        update_data = data.model_dump(exclude_unset=True)
        if obj.is_system and update_data.get("api_key_ref") == MASKED_API_KEY_REF:
            update_data.pop("api_key_ref")
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
    ) -> tuple[list[ChatModel], int]:
        """Return paginated list of chat models."""
        query = select(ChatModel)
        if user_id is not None:
            query = query.where(
                or_(ChatModel.user_id == user_id, ChatModel.is_system.is_(True))
            )
        if provider is not None:
            query = query.where(ChatModel.provider == provider)
        if enabled is not None:
            query = query.where(ChatModel.enabled == enabled)

        count_query = select(func.count()).select_from(query.subquery())
        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        query = query.order_by(ChatModel.created_at.desc()).offset(skip).limit(limit)
        result = await self.db.execute(query)
        items = list(result.scalars().all())
        return items, total

    async def get_default(self) -> ChatModel | None:
        """Return the enabled default chat model, or None if not set."""
        result = await self.db.execute(
            select(ChatModel)
            .where(ChatModel.is_default.is_(True), ChatModel.enabled.is_(True))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _clear_default_models(self, exclude_id: str | UUID | None = None) -> None:
        stmt = update(ChatModel).where(ChatModel.is_default.is_(True))
        if exclude_id is not None:
            stmt = stmt.where(ChatModel.id != exclude_id)
        await self.db.execute(stmt.values(is_default=False))

    async def search(
        self,
        search_term: str,
        user_id: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[ChatModel], int]:
        """Search chat models by name or description."""
        pattern = f"%{search_term}%"
        query = select(ChatModel).where(
            or_(
                ChatModel.name.ilike(pattern),
                ChatModel.description.ilike(pattern),
                ChatModel.model_id.ilike(pattern),
            )
        )
        if user_id is not None:
            query = query.where(
                or_(ChatModel.user_id == user_id, ChatModel.is_system.is_(True))
            )
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await self.db.execute(count_query)
        total = total_result.scalar_one()

        query = query.order_by(ChatModel.created_at.desc()).offset(skip).limit(limit)
        result = await self.db.execute(query)
        items = list(result.scalars().all())
        return items, total
