"""Tenant model for multi-tenancy support."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from structure.extensions.database import get_base

if TYPE_CHECKING:
    from structure.models.auth.user import User

Base = get_base("structure")


class Tenant(Base):
    __tablename__ = "auth_tenant"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Tenant information
    name: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, comment="租户名称"
    )
    description: Mapped[str] = mapped_column(String(500), comment="租户描述")

    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", comment="是否激活"
    )

    # Audit fields
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        comment="更新时间",
    )
    admin_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, comment="管理员ID"
    )
    # Relationships
    users: Mapped[list[User]] = relationship("User", back_populates="tenant")

    def __repr__(self) -> str:
        return f"<Tenant(id={self.id}, name='{self.name}', is_active={self.is_active})>"
