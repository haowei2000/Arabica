#!/usr/bin/env python3
"""
Script to create a default admin user for the application.
This script should be run after the database is initialized.
"""

import asyncio
import os
from pathlib import Path
import sys

# Add the src directory to the path so we can import the app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.extensions.database import get_base, get_session
from aiwen.models.auth.tenant import Tenant
from aiwen.models.auth.user import User
from aiwen.utils.security import hash_password


async def create_admin_user():
    """Create a default admin user if one doesn't already exist."""

    try:
        # Use the database session context manager
        async with get_session("aiwen") as session:
            # Check if default tenant exists, create if not
            result = await session.execute(
                select(Tenant).where(Tenant.name == "default")
            )
            tenant = result.scalar_one_or_none()

            if not tenant:
                print("📝 Creating default tenant...")
                tenant = Tenant(name="default", description="Default tenant")
                session.add(tenant)
                await session.commit()
                await session.refresh(tenant)
                print("✅ Default tenant created")
            else:
                print("✅ Default tenant already exists")

            # Check if admin user already exists
            result = await session.execute(select(User).where(User.username == "admin"))
            admin_user = result.scalar_one_or_none()

            if admin_user:
                print("✅ Admin user already exists")
                return True

            # Create admin user
            print("📝 Creating admin user...")

            # Hash the password
            admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
            hashed_password = hash_password(admin_password)

            # Create the admin user
            admin_user = User(
                username="admin",
                email=os.getenv("ADMIN_EMAIL", "admin@example.com"),
                password_hash=hashed_password,
                tenant_id=tenant.id,
                role="admin",
                is_superuser=True,
                is_active=True,
            )

            session.add(admin_user)
            await session.commit()
            await session.refresh(admin_user)

            print("✅ Admin user created successfully!")
            print("   Username: admin")
            print(f"   Password: {admin_password}")
            print(f"   Email: {admin_user.email}")
            print(f"   Role: {admin_user.role}")
            return True

    except Exception as e:
        print(f"❌ Error creating admin user: {e}")
        return False


def main():
    """Main entry point for the script."""
    print("🔧 Admin User Creation Script")
    print("=" * 30)

    # Run the async function
    result = asyncio.run(create_admin_user())

    if result:
        print("\n🎉 Script completed successfully!")
        return 0
    print("\n💥 Script failed!")
    return 1


if __name__ == "__main__":
    sys.exit(main())
