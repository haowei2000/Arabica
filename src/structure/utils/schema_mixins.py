"""Reusable Pydantic mixins for schema definitions.

This module provides composable mixins to eliminate boilerplate code in
Pydantic schemas, particularly for SQLAlchemy model conversions.

Usage Examples:
    # Simple response schema with all standard features
    class WorkspaceResponse(ResponseMixin, BaseModel):
        id: str
        name: str
        # created_at, updated_at, UUID conversion, ORM config inherited

    # Custom combination
    class ImmutableResource(UUIDConversionMixin, CreatedAtMixin, ORMConfigMixin, BaseModel):
        id: str
        # Only created_at, no updated_at

    # Enum conversion
    class WorkspaceWithEnum(EnumStringMixin, BaseModel):
        status: WorkspaceStatus  # Auto-converts to string
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ORMConfigMixin(BaseModel):
    """Provides Pydantic v2 ORM configuration.

    Enables automatic conversion from SQLAlchemy ORM models to Pydantic schemas.
    Replaces the old `class Config: from_attributes = True` pattern.
    """

    model_config = ConfigDict(from_attributes=True)


class UUIDConversionMixin(BaseModel):
    """Automatically converts UUID fields to strings.

    This mixin eliminates the need for manual UUID-to-string conversion
    validators that appear in 6+ schemas across the codebase.

    Example:
        class UserResponse(UUIDConversionMixin, BaseModel):
            id: str  # Will be converted from UUID automatically
            user_id: str  # Any UUID field works
    """

    @model_validator(mode="before")
    @classmethod
    def convert_uuids(cls, data: Any) -> Any:
        """Convert UUID fields to strings automatically.

        Args:
            data: Raw data from ORM model or dict

        Returns:
            Dict with UUID fields converted to strings
        """
        if hasattr(data, "__dict__"):
            result = {}
            for field_name in cls.model_fields.keys():  # noqa: SIM118
                value = getattr(data, field_name, None)
                if isinstance(value, UUID):
                    result[field_name] = str(value)
                else:
                    result[field_name] = value
            return result
        return data


class TimestampMixin(BaseModel):
    """Provides standard timestamp fields.

    Adds created_at and updated_at fields that appear in 16+ schemas.
    """

    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime | None = Field(None, description="Last update timestamp")


class CreatedAtMixin(BaseModel):
    """Provides only created_at field for immutable resources.

    Use this for resources that are never updated after creation,
    such as audit logs, events, or historical records.
    """

    created_at: datetime = Field(..., description="Creation timestamp")


class EnumStringMixin(BaseModel):
    """Automatically converts Enum fields to their string values.

    This mixin eliminates manual `.value` extraction in routers and schemas.

    Example:
        class WorkspaceResponse(EnumStringMixin, BaseModel):
            status: str  # Will extract .value from WorkspaceStatus enum
    """

    @model_validator(mode="before")
    @classmethod
    def extract_enum_values(cls, data: Any) -> Any:
        """Extract .value from Enum fields automatically.

        Args:
            data: Raw data from ORM model or dict

        Returns:
            Dict with Enum fields converted to strings
        """
        if hasattr(data, "__dict__"):
            result = {}
            for field_name in cls.model_fields.keys():  # noqa: SIM118
                value = getattr(data, field_name, None)
                if isinstance(value, Enum):
                    result[field_name] = value.value
                else:
                    result[field_name] = value
            return result
        return data


class ResponseMixin(UUIDConversionMixin, TimestampMixin, ORMConfigMixin):
    """Combined mixin for typical response schemas.

    This is the most commonly used mixin, combining:
    - UUID to string conversion
    - created_at/updated_at timestamps
    - ORM configuration

    Use this for standard CRUD response schemas.

    Example:
        class WorkspaceResponse(ResponseMixin, BaseModel):
            id: str
            name: str
            description: str | None = None
            # All boilerplate inherited!
    """

    pass


class ResponseWithEnumMixin(
    UUIDConversionMixin, TimestampMixin, EnumStringMixin, ORMConfigMixin
):
    """Combined mixin for response schemas with enum fields.

    Adds enum value extraction to the standard ResponseMixin.

    Example:
        class WorkspaceResponse(ResponseWithEnumMixin, BaseModel):
            id: str
            status: str  # Auto-extracts from WorkspaceStatus enum
    """

    pass
