"""CRUD operations for User model."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.models.auth.user import User
from structure.schemas.auth.user import UserCreate, UserInDB, UserResponse


class UserCRUD:
    """CRUD operations for User model."""

    def __init__(self, db_session: AsyncSession):
        """
        Initialize UserCRUD with database session.

        Args:
            db_session: SQLAlchemy async session
        """
        self.db_session = db_session

    async def create_user(
        self, user_data: UserCreate, tenant_id: UUID, auto_commit: bool = True
    ) -> User:
        """
        Create a new user.

        Args:
            user_data: User creation data
            tenant_id: Tenant ID for the user
            auto_commit: If True (default), immediately commit the transaction.
                         If False, only flush changes.

        Returns:
            Created User instance
        """
        from structure.utils.security import hash_password

        # Hash the password
        hashed_password = hash_password(user_data.password)

        user = User(
            username=user_data.username,
            email=user_data.email,
            phone=user_data.phone,
            password_hash=hashed_password,
            tenant_id=tenant_id,
            role="user",  # Default role
        )

        self.db_session.add(user)

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        await self.db_session.refresh(user)
        return user

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        """
        Get a user by its UUID.

        Args:
            user_id: UUID of the user

        Returns:
            User instance if found, None otherwise
        """
        result = await self.db_session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_user_by_username(self, username: str) -> User | None:
        """
        Get a user by its username.

        Args:
            username: Username of the user

        Returns:
            User instance if found, None otherwise
        """
        result = await self.db_session.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()

    async def get_user_by_email(self, email: str) -> User | None:
        """
        Get a user by its email.

        Args:
            email: Email of the user

        Returns:
            User instance if found, None otherwise
        """
        result = await self.db_session.execute(
            select(User).where(func.lower(User.email) == email.lower())
        )
        return result.scalar_one_or_none()

    async def list_users(
        self,
        skip: int = 0,
        limit: int = 100,
        tenant_id: UUID | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[User], int]:
        """
        List all users with pagination.

        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return
            tenant_id: Filter by tenant ID
            is_active: Filter by active status

        Returns:
            Tuple of (list of users, total count)
        """
        # Base query
        stmt = select(User)
        count_stmt = select(func.count(User.id))

        # Apply filters
        if tenant_id:
            stmt = stmt.where(User.tenant_id == tenant_id)
            count_stmt = count_stmt.where(User.tenant_id == tenant_id)

        if is_active is not None:
            stmt = stmt.where(User.is_active == is_active)
            count_stmt = count_stmt.where(User.is_active == is_active)

        # Get total count
        count_result = await self.db_session.execute(count_stmt)
        total = count_result.scalar() or 0

        # Apply pagination and ordering
        stmt = stmt.order_by(User.created_at.desc()).offset(skip).limit(limit)

        result = await self.db_session.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def update_user(
        self,
        user_id: UUID,
        username: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        role: str | None = None,
        is_active: bool | None = None,
        is_superuser: bool | None = None,
        auto_commit: bool = True,
    ) -> User | None:
        """
        Update an existing user.

        Args:
            user_id: ID of the user to update
            username: New username (optional)
            email: New email (optional)
            phone: New phone (optional)
            role: New role (optional)
            is_active: New active status (optional)
            is_superuser: New superuser status (optional)
            auto_commit: If True (default), immediately commit the transaction.
                         If False, only flush changes.

        Returns:
            Updated User instance or None if not found
        """
        user = await self.get_user_by_id(user_id)
        if not user:
            return None

        # Update fields if provided
        if username is not None:
            user.username = username
        if email is not None:
            user.email = email
        if phone is not None:
            user.phone = phone
        if role is not None:
            user.role = role
        if is_active is not None:
            user.is_active = is_active
        if is_superuser is not None:
            user.is_superuser = is_superuser

        user.updated_at = func.now()

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        await self.db_session.refresh(user)
        return user

    async def delete_user(self, user_id: UUID, auto_commit: bool = True) -> bool:
        """
        Delete a user by ID.

        Args:
            user_id: ID of the user to delete
            auto_commit: If True (default), immediately commit the transaction.
                         If False, only flush changes.

        Returns:
            True if deleted, False if not found
        """
        user = await self.get_user_by_id(user_id)
        if not user:
            return False

        await self.db_session.delete(user)

        if auto_commit:
            await self.db_session.commit()
        else:
            await self.db_session.flush()

        return True
