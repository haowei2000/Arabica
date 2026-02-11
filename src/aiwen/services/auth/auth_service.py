"""Authentication service for user management."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.auth.tenant import Tenant
from aiwen.models.auth.user import User
from aiwen.schemas.auth.user import UserCreate
from aiwen.utils.security import hash_password, verify_password


class AuthService:
    """Service for handling user authentication."""

    def __init__(self, db_session: AsyncSession):
        """
        Initialize the AuthService.

        Args:
            db_session: Database session
        """
        self.db = db_session

    async def get_user_by_username(self, username: str) -> User | None:
        """
        Get a user by username.

        Args:
            username: Username to search for

        Returns:
            User object if found, None otherwise
        """
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def get_user_by_email(self, email: str) -> User | None:
        """
        Get a user by email.

        Args:
            email: Email to search for

        Returns:
            User object if found, None otherwise
        """
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        """
        Get a user by ID.

        Args:
            user_id: User ID to search for

        Returns:
            User object if found, None otherwise
        """
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def authenticate_user(self, username: str, password: str) -> User | None:
        """
        Authenticate a user by username and password.

        Args:
            username: Username or email
            password: Plain text password

        Returns:
            User object if authentication succeeds, None otherwise
        """
        # Try to find user by username first
        user = await self.get_user_by_username(username)

        # If not found, try to find by email
        if not user:
            user = await self.get_user_by_email(username)

        # If user not found or inactive, return None
        if not user or not user.is_active:
            return None

        # Verify password
        if not verify_password(password, user.password_hash):
            return None

        return user

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
            Created user object
        """
        # Hash the password
        hashed_password = hash_password(user_data.password)

        # Create user object
        user = User(
            username=user_data.username,
            email=user_data.email,
            phone=user_data.phone,
            password_hash=hashed_password,
            tenant_id=tenant_id,
            role="user",  # Default role
        )

        # Add to database
        self.db.add(user)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(user)

        return user

    async def get_tenant_by_name(self, name: str) -> Tenant | None:
        """
        Get a tenant by name.

        Args:
            name: Tenant name to search for

        Returns:
            Tenant object if found, None otherwise
        """
        result = await self.db.execute(select(Tenant).where(Tenant.name == name))
        return result.scalar_one_or_none()

    async def create_tenant(
        self,
        name: str,
        admin_id,
        description: str | None = None,
        auto_commit: bool = True,
    ) -> Tenant:
        """
        Create a new tenant.

        Args:
            name: Tenant name
            admin_id: Admin user ID
            description: Tenant description
            auto_commit: If True (default), immediately commit the transaction.
                         If False, only flush changes.

        Returns:
            Created tenant object
        """
        tenant = Tenant(name=name, admin_id=admin_id, description=description)

        self.db.add(tenant)

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(tenant)

        return tenant
