# aiwen/services/agents/app_crud.py
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.agents.app import App
from aiwen.schemas.agents.app import AppCreate, AppUpdate


class AppCRUD:
    """CRUD operations for App (Agent) model."""

    def __init__(self, db_session: AsyncSession):
        """
        Initialize AppCRUD with database session.

        Args:
            db_session: SQLAlchemy async session
        """
        self.db_session = db_session

    def normalize_uuid_to_str(self, val: str | UUID) -> str:
        """
        Normalize UUID to string format for internal storage.
        This method handles both string and UUID inputs.
        """
        if isinstance(val, str):
            return val
        if isinstance(val, UUID):
            return str(val)
        raise ValueError(f"Invalid UUID type: {type(val)}")

    async def create_app(self, data: AppCreate, auto_commit: bool = False) -> App:
        """
        Create a new app.

        Args:
            data: App creation data
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            Created App instance
        """
        # Normalize user_id if provided
        user_id = None
        if data.user_id:
            user_id = self.normalize_uuid_to_str(data.user_id)

        app = App(
            id=uuid4(),
            app_code=data.app_code,
            agent_template_id=data.agent_template_id,
            user_id=user_id,  # Add user_id from the request
            enabled=data.enabled,
            config=data.config or {},
            version=data.version,
            created_at=datetime.now(UTC),
        )

        self.db_session.add(app)

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        await self.db_session.refresh(app)
        return app

    async def get_app(self, app_id: UUID) -> App | None:
        """
        Get an app by its UUID.

        Args:
            app_id: UUID of the app

        Returns:
            App instance if found, None otherwise
        """
        result = await self.db_session.execute(select(App).where(App.id == app_id))
        return result.scalar_one_or_none()

    async def get_app_by_code(self, app_code: str) -> App | None:
        """
        Get an app by its unique code.

        Args:
            app_code: Unique code of the app

        Returns:
            App instance if found, None otherwise
        """
        result = await self.db_session.execute(
            select(App).where(App.app_code == app_code)
        )
        return result.scalar_one_or_none()

    async def list_apps(
            self,
            skip: int = 0,
            limit: int = 100,
            enabled_only: bool = False,
            user_id: UUID | None = None,  # Add filter by user_id
    ) -> tuple[list[App], int]:
        """
        List all apps with pagination.

        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return
            enabled_only: If True, only return enabled apps
            user_id: If provided, only return apps owned by this user

        Returns:
            Tuple of (list of apps, total count)
        """
        # Base query
        stmt = select(App)
        count_stmt = select(func.count(App.id))

        # Apply filters
        if enabled_only:
            stmt = stmt.where(App.enabled == True)
            count_stmt = count_stmt.where(App.enabled == True)

        if user_id:
            stmt = stmt.where(App.user_id == user_id)
            count_stmt = count_stmt.where(App.user_id == user_id)

        # Get total count
        count_result = await self.db_session.execute(count_stmt)
        total = count_result.scalar() or 0

        # Apply pagination and ordering
        stmt = stmt.order_by(App.created_at.desc()).offset(skip).limit(limit)

        result = await self.db_session.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def update_app(
            self, app_id: UUID, data: AppUpdate, auto_commit: bool = False
    ) -> App | None:
        """
        Update an existing app.

        Args:
            app_id:
            data: Update data
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            Updated App instance or None if not found
        """
        app = await self.get_app(app_id)
        if not app:
            return None

        # Update fields if provided
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(app, key, value)

        app.updated_at = datetime.now(UTC)

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        await self.db_session.refresh(app)
        return app

    async def delete_app(self, app_id: UUID, auto_commit: bool = False) -> bool:
        """
        Delete an app by app_id.

        Args:
            app_id: The app_id to delete
            auto_commit: If True, immediately commit the transaction.
                         If False (default), only flush changes.

        Returns:
            True if deleted, False if not found
        """
        app = await self.get_app(app_id)
        if not app:
            return False

        await self.db_session.delete(app)

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        return True

    async def filter_apps_by_template(
            self, agent_template_id: UUID, skip: int = 0, limit: int = 100
    ) -> tuple[list[App], int]:
        """
        Get all apps using a specific agent template.

        Args:
            agent_template_id: ID of the agent template
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of apps, total count)
        """
        # Base query
        stmt = select(App).where(App.agent_template_id == agent_template_id)
        count_stmt = select(func.count(App.id)).where(
            App.agent_template_id == agent_template_id
        )

        # Get total count
        count_result = await self.db_session.execute(count_stmt)
        total = count_result.scalar() or 0

        # Apply pagination and ordering
        stmt = stmt.order_by(App.created_at.desc()).offset(skip).limit(limit)

        result = await self.db_session.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def get_apps_by_user(
            self, user_id: UUID, skip: int = 0, limit: int = 100
    ) -> tuple[list[App], int]:
        """
        Get all apps created by a specific user.

        Args:
            user_id: ID of the user
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            Tuple of (list of apps, total count)
        """
        # Base query
        stmt = select(App).where(App.user_id == user_id)
        count_stmt = select(func.count(App.id)).where(App.user_id == user_id)

        # Get total count
        count_result = await self.db_session.execute(count_stmt)
        total = count_result.scalar() or 0

        # Apply pagination and ordering
        stmt = stmt.order_by(App.created_at.desc()).offset(skip).limit(limit)

        result = await self.db_session.execute(stmt)
        items = list(result.scalars().all())

        return items, total
