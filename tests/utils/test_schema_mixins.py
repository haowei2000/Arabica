"""Tests for schema_mixins module.

Tests all 6 mixins:
- UUIDConversionMixin
- TimestampMixin
- CreatedAtMixin
- EnumStringMixin
- ORMConfigMixin
- ResponseMixin
- ResponseWithEnumMixin
"""

from datetime import UTC, datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
import pytest

from aiwen.utils.schema_mixins import (
    CreatedAtMixin,
    EnumStringMixin,
    ORMConfigMixin,
    ResponseMixin,
    ResponseWithEnumMixin,
    TimestampMixin,
    UUIDConversionMixin,
)

# Test fixtures


class Status(str, Enum):
    """Mock enum for testing."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"


class MockModel:
    """Mock SQLAlchemy model for testing."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


# Test UUIDConversionMixin


class TestUUIDConversionMixin:
    """Test UUIDConversionMixin."""

    def test_single_uuid_conversion(self):
        """Should convert single UUID field to string."""

        class TestSchema(UUIDConversionMixin, BaseModel):
            id: str
            name: str

        model = MockModel(id=uuid4(), name="test")
        schema = TestSchema.model_validate(model)

        assert isinstance(schema.id, str)
        assert schema.name == "test"
        # Verify it's a valid UUID string
        UUID(schema.id)

    def test_multiple_uuid_conversion(self):
        """Should convert multiple UUID fields to strings."""

        class TestSchema(UUIDConversionMixin, BaseModel):
            id: str
            user_id: str
            workspace_id: str
            name: str

        model = MockModel(
            id=uuid4(),
            user_id=uuid4(),
            workspace_id=uuid4(),
            name="test",
        )
        schema = TestSchema.model_validate(model)

        assert isinstance(schema.id, str)
        assert isinstance(schema.user_id, str)
        assert isinstance(schema.workspace_id, str)
        # Verify all are valid UUID strings
        UUID(schema.id)
        UUID(schema.user_id)
        UUID(schema.workspace_id)

    def test_optional_uuid_field(self):
        """Should handle optional UUID fields."""

        class TestSchema(UUIDConversionMixin, BaseModel):
            id: str
            parent_id: str | None = None

        # With value
        model_with = MockModel(id=uuid4(), parent_id=uuid4())
        schema_with = TestSchema.model_validate(model_with)
        assert isinstance(schema_with.parent_id, str)

        # Without value
        model_without = MockModel(id=uuid4(), parent_id=None)
        schema_without = TestSchema.model_validate(model_without)
        assert schema_without.parent_id is None

    def test_dict_input_passthrough(self):
        """Should pass through dict input without modification."""

        class TestSchema(UUIDConversionMixin, BaseModel):
            id: str
            name: str

        data = {"id": "already-a-string", "name": "test"}
        schema = TestSchema.model_validate(data)

        assert schema.id == "already-a-string"
        assert schema.name == "test"

    def test_missing_field(self):
        """Should handle missing fields gracefully."""

        class TestSchema(UUIDConversionMixin, BaseModel):
            id: str
            name: str
            optional: str | None = None

        model = MockModel(id=uuid4(), name="test")
        schema = TestSchema.model_validate(model)

        assert schema.optional is None


# Test TimestampMixin


class TestTimestampMixin:
    """Test TimestampMixin."""

    def test_timestamp_fields_present(self):
        """Should provide created_at and updated_at fields."""

        class TestSchema(TimestampMixin, BaseModel):
            id: str

        now = datetime.now(UTC)
        data = {"id": "123", "created_at": now, "updated_at": now}
        schema = TestSchema.model_validate(data)

        assert schema.created_at == now
        assert schema.updated_at == now

    def test_updated_at_optional(self):
        """updated_at should be optional."""

        class TestSchema(TimestampMixin, BaseModel):
            id: str

        now = datetime.now(UTC)
        data = {"id": "123", "created_at": now}
        schema = TestSchema.model_validate(data)

        assert schema.created_at == now
        assert schema.updated_at is None

    def test_created_at_required(self):
        """created_at should be required."""

        class TestSchema(TimestampMixin, BaseModel):
            id: str

        data = {"id": "123"}

        with pytest.raises(ValidationError):
            TestSchema.model_validate(data)


# Test CreatedAtMixin


class TestCreatedAtMixin:
    """Test CreatedAtMixin."""

    def test_only_created_at(self):
        """Should provide only created_at field."""

        class TestSchema(CreatedAtMixin, BaseModel):
            id: str

        now = datetime.now(UTC)
        data = {"id": "123", "created_at": now}
        schema = TestSchema.model_validate(data)

        assert schema.created_at == now
        assert not hasattr(schema, "updated_at")

    def test_created_at_required(self):
        """created_at should be required."""

        class TestSchema(CreatedAtMixin, BaseModel):
            id: str

        data = {"id": "123"}

        with pytest.raises(ValidationError):
            TestSchema.model_validate(data)


# Test EnumStringMixin


class TestEnumStringMixin:
    """Test EnumStringMixin."""

    def test_enum_value_extraction(self):
        """Should extract .value from Enum fields."""

        class TestSchema(EnumStringMixin, BaseModel):
            status: str
            name: str

        model = MockModel(status=Status.ACTIVE, name="test")
        schema = TestSchema.model_validate(model)

        assert schema.status == "active"
        assert schema.name == "test"

    def test_multiple_enum_fields(self):
        """Should extract values from multiple Enum fields."""

        class TestSchema(EnumStringMixin, BaseModel):
            status: str
            priority: str

        class Priority(str, Enum):
            HIGH = "high"
            LOW = "low"

        model = MockModel(status=Status.ACTIVE, priority=Priority.HIGH)
        schema = TestSchema.model_validate(model)

        assert schema.status == "active"
        assert schema.priority == "high"

    def test_optional_enum_field(self):
        """Should handle optional Enum fields."""

        class TestSchema(EnumStringMixin, BaseModel):
            status: str | None = None

        # With value
        model_with = MockModel(status=Status.ACTIVE)
        schema_with = TestSchema.model_validate(model_with)
        assert schema_with.status == "active"

        # Without value
        model_without = MockModel(status=None)
        schema_without = TestSchema.model_validate(model_without)
        assert schema_without.status is None

    def test_non_enum_passthrough(self):
        """Should pass through non-Enum values unchanged."""

        class TestSchema(EnumStringMixin, BaseModel):
            status: str
            count: int

        model = MockModel(status="plain_string", count=42)
        schema = TestSchema.model_validate(model)

        assert schema.status == "plain_string"
        assert schema.count == 42


# Test ORMConfigMixin


class TestORMConfigMixin:
    """Test ORMConfigMixin."""

    def test_from_attributes_config(self):
        """Should enable from_attributes in model_config."""

        class TestSchema(ORMConfigMixin, BaseModel):
            id: str
            name: str

        model = MockModel(id="123", name="test")
        schema = TestSchema.model_validate(model)

        assert schema.id == "123"
        assert schema.name == "test"

    def test_model_config_present(self):
        """Should have model_config attribute."""

        class TestSchema(ORMConfigMixin, BaseModel):
            id: str

        assert hasattr(TestSchema, "model_config")
        assert TestSchema.model_config.get("from_attributes") is True


# Test ResponseMixin


class TestResponseMixin:
    """Test ResponseMixin (combination of UUID, Timestamp, ORM)."""

    def test_combined_features(self):
        """Should combine UUID conversion, timestamps, and ORM config."""

        class TestSchema(ResponseMixin, BaseModel):
            id: str
            name: str

        now = datetime.now(UTC)
        model = MockModel(
            id=uuid4(),
            name="test",
            created_at=now,
            updated_at=now,
        )
        schema = TestSchema.model_validate(model)

        # UUID conversion
        assert isinstance(schema.id, str)
        UUID(schema.id)

        # Timestamps
        assert schema.created_at == now
        assert schema.updated_at == now

        # Regular fields
        assert schema.name == "test"

    def test_real_world_scenario(self):
        """Test with a realistic workspace-like model."""

        class WorkspaceResponse(ResponseMixin, BaseModel):
            id: str
            user_id: str
            name: str
            description: str | None = None

        now = datetime.now(UTC)
        model = MockModel(
            id=uuid4(),
            user_id=uuid4(),
            name="My Workspace",
            description="Test workspace",
            created_at=now,
            updated_at=now,
        )
        schema = WorkspaceResponse.model_validate(model)

        assert isinstance(schema.id, str)
        assert isinstance(schema.user_id, str)
        assert schema.name == "My Workspace"
        assert schema.description == "Test workspace"
        assert schema.created_at == now
        assert schema.updated_at == now


# Test ResponseWithEnumMixin


class TestResponseWithEnumMixin:
    """Test ResponseWithEnumMixin (combination with Enum)."""

    def test_combined_with_enum(self):
        """Should combine UUID, Timestamp, Enum, and ORM features."""

        class TestSchema(ResponseWithEnumMixin, BaseModel):
            id: str
            status: str
            name: str

        now = datetime.now(UTC)
        model = MockModel(
            id=uuid4(),
            status=Status.ACTIVE,
            name="test",
            created_at=now,
            updated_at=now,
        )
        schema = TestSchema.model_validate(model)

        # UUID conversion
        assert isinstance(schema.id, str)
        UUID(schema.id)

        # Enum extraction
        assert schema.status == "active"

        # Timestamps
        assert schema.created_at == now
        assert schema.updated_at == now

        # Regular fields
        assert schema.name == "test"

    def test_workspace_with_status(self):
        """Test with a realistic workspace model including status."""

        class WorkspaceResponse(ResponseWithEnumMixin, BaseModel):
            id: str
            name: str
            status: str

        now = datetime.now(UTC)
        model = MockModel(
            id=uuid4(),
            name="My Workspace",
            status=Status.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        schema = WorkspaceResponse.model_validate(model)

        assert isinstance(schema.id, str)
        assert schema.name == "My Workspace"
        assert schema.status == "active"
        assert schema.created_at == now


# Edge cases and integration tests


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_model(self):
        """Should handle models with minimal required fields."""

        class TestSchema(UUIDConversionMixin, ORMConfigMixin, BaseModel):
            id: str = "default"

        model = MockModel()
        # Should not crash, will use default values
        schema = TestSchema.model_validate({"id": "test"})
        assert schema.id == "test"

    def test_extra_fields_ignored(self):
        """Should ignore extra fields not in schema."""

        class TestSchema(UUIDConversionMixin, BaseModel):
            id: str

        model = MockModel(id=uuid4(), extra_field="ignored")
        schema = TestSchema.model_validate(model)

        assert isinstance(schema.id, str)
        assert not hasattr(schema, "extra_field")

    def test_multiple_inheritance(self):
        """Should work with multiple mixin inheritance."""

        class TestSchema(
            UUIDConversionMixin,
            EnumStringMixin,
            TimestampMixin,
            ORMConfigMixin,
            BaseModel,
        ):
            id: str
            status: str

        now = datetime.now(UTC)
        model = MockModel(
            id=uuid4(),
            status=Status.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        schema = TestSchema.model_validate(model)

        assert isinstance(schema.id, str)
        assert schema.status == "active"
        assert schema.created_at == now
