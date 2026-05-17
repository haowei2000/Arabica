"""Tests for model_converters module.

Tests all converter functions:
- normalize_uuid
- extract_enum_value
- extract_enum_dict
- convert_uuid_fields
- model_to_schema
- models_to_schemas
- model_to_dict
"""

from datetime import UTC, datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
import pytest

from structure.utils.model_converters import (
    convert_uuid_fields,
    extract_enum_dict,
    extract_enum_value,
    model_to_dict,
    model_to_schema,
    models_to_schemas,
    normalize_uuid,
)

# Test fixtures


class Status(str, Enum):
    """Mock enum for testing."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class Priority(str, Enum):
    """Another mock enum."""

    HIGH = "high"
    LOW = "low"


class MockModel:
    """Mock SQLAlchemy model."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class SimpleSchema(BaseModel):
    """Simple test schema."""

    model_config = {"from_attributes": True}

    id: str
    name: str


class SchemaWithOptional(BaseModel):
    """Schema with optional fields."""

    model_config = {"from_attributes": True}

    id: str
    name: str
    description: str | None = None


class SchemaWithTimestamps(BaseModel):
    """Schema with timestamps."""

    model_config = {"from_attributes": True}

    id: str
    name: str
    created_at: datetime
    updated_at: datetime | None = None


class SchemaWithExtra(BaseModel):
    """Schema with extra computed field."""

    model_config = {"from_attributes": True}

    id: str
    name: str
    score: float


# Test normalize_uuid


class TestNormalizeUuid:
    """Test normalize_uuid function."""

    def test_uuid_to_string(self):
        """Should convert UUID to string."""
        uuid_obj = uuid4()
        result = normalize_uuid(uuid_obj)

        assert isinstance(result, str)
        assert result == str(uuid_obj)
        # Verify it's a valid UUID string
        UUID(result)

    def test_string_passthrough(self):
        """Should pass through string unchanged."""
        uuid_str = str(uuid4())
        result = normalize_uuid(uuid_str)

        assert result == uuid_str

    def test_none_passthrough(self):
        """Should return None for None input."""
        result = normalize_uuid(None)
        assert result is None


# Test extract_enum_value


class TestExtractEnumValue:
    """Test extract_enum_value function."""

    def test_enum_extraction(self):
        """Should extract .value from Enum."""
        result = extract_enum_value(Status.ACTIVE)
        assert result == "active"

    def test_non_enum_passthrough(self):
        """Should pass through non-Enum values."""
        assert extract_enum_value("string") == "string"
        assert extract_enum_value(42) == 42
        assert extract_enum_value(None) is None
        assert extract_enum_value([1, 2, 3]) == [1, 2, 3]


# Test extract_enum_dict


class TestExtractEnumDict:
    """Test extract_enum_dict function."""

    def test_enum_dict_extraction(self):
        """Should extract enum values from dict."""
        data = {
            "status": Status.ACTIVE,
            "priority": Priority.HIGH,
            "name": "test",
            "count": 42,
        }
        result = extract_enum_dict(data)

        assert result["status"] == "active"
        assert result["priority"] == "high"
        assert result["name"] == "test"
        assert result["count"] == 42

    def test_empty_dict(self):
        """Should handle empty dict."""
        result = extract_enum_dict({})
        assert result == {}

    def test_no_enums(self):
        """Should pass through dict with no enums."""
        data = {"name": "test", "count": 42}
        result = extract_enum_dict(data)
        assert result == data


# Test convert_uuid_fields


class TestConvertUuidFields:
    """Test convert_uuid_fields function."""

    def test_basic_conversion(self):
        """Should convert UUID fields to strings."""
        model = MockModel(
            id=uuid4(),
            name="test",
        )
        result = convert_uuid_fields(model)

        assert isinstance(result["id"], str)
        UUID(result["id"])  # Verify valid UUID string
        assert result["name"] == "test"

    def test_multiple_uuids(self):
        """Should convert multiple UUID fields."""
        model = MockModel(
            id=uuid4(),
            user_id=uuid4(),
            workspace_id=uuid4(),
        )
        result = convert_uuid_fields(model)

        assert isinstance(result["id"], str)
        assert isinstance(result["user_id"], str)
        assert isinstance(result["workspace_id"], str)

    def test_enum_conversion(self):
        """Should convert Enum fields to values."""
        model = MockModel(
            id=uuid4(),
            status=Status.ACTIVE,
        )
        result = convert_uuid_fields(model)

        assert isinstance(result["id"], str)
        assert result["status"] == "active"

    def test_mixed_types(self):
        """Should handle mixed field types."""
        now = datetime.now(UTC)
        model = MockModel(
            id=uuid4(),
            name="test",
            count=42,
            active=True,
            score=3.14,
            created_at=now,
            status=Status.ACTIVE,
        )
        result = convert_uuid_fields(model)

        assert isinstance(result["id"], str)
        assert result["name"] == "test"
        assert result["count"] == 42
        assert result["active"] is True
        assert result["score"] == 3.14
        assert result["created_at"] == now
        assert result["status"] == "active"

    def test_skip_private_fields(self):
        """Should skip fields starting with underscore."""
        model = MockModel(
            id=uuid4(),
            name="test",
            _private="hidden",
        )
        result = convert_uuid_fields(model)

        assert "id" in result
        assert "name" in result
        assert "_private" not in result

    def test_none_values(self):
        """Should handle None values."""
        model = MockModel(
            id=uuid4(),
            name=None,
            description=None,
        )
        result = convert_uuid_fields(model)

        assert isinstance(result["id"], str)
        assert result["name"] is None
        assert result["description"] is None

    def test_non_model_input(self):
        """Should return empty dict for non-model input."""
        result = convert_uuid_fields("not a model")
        assert result == {}


# Test model_to_schema


class TestModelToSchema:
    """Test model_to_schema function."""

    def test_basic_conversion(self):
        """Should convert model to schema."""
        model = MockModel(
            id=uuid4(),
            name="test",
        )
        schema = model_to_schema(SimpleSchema, model)

        assert isinstance(schema, SimpleSchema)
        assert isinstance(schema.id, str)
        assert schema.name == "test"

    def test_with_optional_fields(self):
        """Should handle optional fields."""
        # With description
        model_with = MockModel(
            id=uuid4(),
            name="test",
            description="A test",
        )
        schema_with = model_to_schema(SchemaWithOptional, model_with)
        assert schema_with.description == "A test"

        # Without description
        model_without = MockModel(
            id=uuid4(),
            name="test",
        )
        schema_without = model_to_schema(SchemaWithOptional, model_without)
        assert schema_without.description is None

    def test_with_exclude(self):
        """Should exclude specified fields."""
        model = MockModel(
            id=uuid4(),
            name="test",
            password="secret",
        )

        # This will fail validation since 'name' is required
        with pytest.raises(ValidationError):
            model_to_schema(SimpleSchema, model, exclude={"name"})

    def test_with_extra_fields(self):
        """Should add extra computed fields."""
        model = MockModel(
            id=uuid4(),
            name="test",
        )
        schema = model_to_schema(
            SchemaWithExtra,
            model,
            extra={"score": 0.95},
        )

        assert schema.score == 0.95

    def test_with_timestamps(self):
        """Should handle timestamp fields."""
        now = datetime.now(UTC)
        model = MockModel(
            id=uuid4(),
            name="test",
            created_at=now,
            updated_at=now,
        )
        schema = model_to_schema(SchemaWithTimestamps, model)

        assert schema.created_at == now
        assert schema.updated_at == now

    def test_real_world_scenario(self):
        """Test realistic conversion with all features."""

        class ContextWithScore(BaseModel):
            model_config = {"from_attributes": True}

            id: str
            user_id: str
            content: str
            score: float

        model = MockModel(
            id=uuid4(),
            user_id=uuid4(),
            content="Test content",
        )
        schema = model_to_schema(
            ContextWithScore,
            model,
            extra={"score": 0.87},
        )

        assert isinstance(schema.id, str)
        assert isinstance(schema.user_id, str)
        assert schema.content == "Test content"
        assert schema.score == 0.87


# Test models_to_schemas


class TestModelsToSchemas:
    """Test models_to_schemas function."""

    def test_basic_list_conversion(self):
        """Should convert list of models to schemas."""
        models = [
            MockModel(id=uuid4(), name="model1"),
            MockModel(id=uuid4(), name="model2"),
            MockModel(id=uuid4(), name="model3"),
        ]
        schemas = models_to_schemas(SimpleSchema, models)

        assert len(schemas) == 3
        assert all(isinstance(s, SimpleSchema) for s in schemas)
        assert schemas[0].name == "model1"
        assert schemas[1].name == "model2"
        assert schemas[2].name == "model3"

    def test_empty_list(self):
        """Should handle empty list."""
        schemas = models_to_schemas(SimpleSchema, [])
        assert schemas == []

    def test_with_exclude(self):
        """Should exclude fields from all items."""
        models = [
            MockModel(id=uuid4(), name="model1"),
            MockModel(id=uuid4(), name="model2"),
        ]

        # Excluding required field will fail validation
        with pytest.raises(ValidationError):
            models_to_schemas(SimpleSchema, models, exclude={"name"})

    def test_with_extra_factory(self):
        """Should add dynamic extra fields using factory."""
        models = [
            MockModel(id=uuid4(), name="model1"),
            MockModel(id=uuid4(), name="model2"),
        ]
        scores = [0.95, 0.87]

        schemas = models_to_schemas(
            SchemaWithExtra,
            models,
            extra_factory=lambda model, idx: {"score": scores[idx]},
        )

        assert len(schemas) == 2
        assert schemas[0].score == 0.95
        assert schemas[1].score == 0.87

    def test_context_search_scenario(self):
        """Test realistic scenario from context search endpoint."""

        class ContextWithScore(BaseModel):
            model_config = {"from_attributes": True}

            id: str
            content: str
            score: float

        # Simulating vector search results
        contexts = [
            MockModel(id=uuid4(), content="ContextSchema 1"),
            MockModel(id=uuid4(), content="ContextSchema 2"),
            MockModel(id=uuid4(), content="ContextSchema 3"),
        ]
        scores = [0.95, 0.87, 0.72]

        schemas = models_to_schemas(
            ContextWithScore,
            contexts,
            extra_factory=lambda ctx, idx: {"score": scores[idx]},
        )

        assert len(schemas) == 3
        assert schemas[0].content == "ContextSchema 1"
        assert schemas[0].score == 0.95
        assert schemas[1].content == "ContextSchema 2"
        assert schemas[1].score == 0.87

    def test_extra_factory_with_model_access(self):
        """extra_factory should have access to model instance."""

        class SchemaWithDerived(BaseModel):
            model_config = {"from_attributes": True}

            id: str
            name: str
            name_length: int

        models = [
            MockModel(id=uuid4(), name="short"),
            MockModel(id=uuid4(), name="much longer name"),
        ]

        schemas = models_to_schemas(
            SchemaWithDerived,
            models,
            extra_factory=lambda model, idx: {"name_length": len(model.name)},
        )

        assert schemas[0].name_length == 5
        assert schemas[1].name_length == 16


# Test model_to_dict


class TestModelToDict:
    """Test model_to_dict function."""

    def test_basic_conversion(self):
        """Should convert model to dict."""
        model = MockModel(
            id=uuid4(),
            name="test",
        )
        result = model_to_dict(model)

        assert isinstance(result, dict)
        assert isinstance(result["id"], str)
        assert result["name"] == "test"

    def test_with_exclude(self):
        """Should exclude specified fields."""
        model = MockModel(
            id=uuid4(),
            name="test",
            password="secret",
        )
        result = model_to_dict(model, exclude={"password"})

        assert "id" in result
        assert "name" in result
        assert "password" not in result

    def test_with_include(self):
        """Should include only specified fields."""
        model = MockModel(
            id=uuid4(),
            name="test",
            description="desc",
            extra="other",
        )
        result = model_to_dict(model, include={"id", "name"})

        assert "id" in result
        assert "name" in result
        assert "description" not in result
        assert "extra" not in result

    def test_include_and_exclude(self):
        """exclude should take precedence over include."""
        model = MockModel(
            id=uuid4(),
            name="test",
            description="desc",
        )
        result = model_to_dict(
            model,
            include={"id", "name", "description"},
            exclude={"description"},
        )

        assert "id" in result
        assert "name" in result
        assert "description" not in result

    def test_uuid_conversion_in_dict(self):
        """Should convert UUIDs to strings in dict."""
        model = MockModel(
            id=uuid4(),
            user_id=uuid4(),
        )
        result = model_to_dict(model)

        assert isinstance(result["id"], str)
        assert isinstance(result["user_id"], str)
        # Verify valid UUID strings
        UUID(result["id"])
        UUID(result["user_id"])

    def test_enum_conversion_in_dict(self):
        """Should convert Enums to values in dict."""
        model = MockModel(
            id=uuid4(),
            status=Status.ACTIVE,
        )
        result = model_to_dict(model)

        assert result["status"] == "active"


# Integration tests


class TestIntegration:
    """Integration tests simulating real-world usage."""

    def test_workspace_response_pattern(self):
        """Test pattern used in workspace endpoints."""

        class WorkspaceResponse(BaseModel):
            model_config = {"from_attributes": True}

            id: str
            user_id: str
            name: str
            description: str | None = None
            created_at: datetime
            updated_at: datetime | None = None

        now = datetime.now(UTC)
        workspace = MockModel(
            id=uuid4(),
            user_id=uuid4(),
            name="My Workspace",
            description="Test workspace",
            created_at=now,
            updated_at=now,
        )

        response = model_to_schema(WorkspaceResponse, workspace)

        assert isinstance(response.id, str)
        assert isinstance(response.user_id, str)
        assert response.name == "My Workspace"
        assert response.description == "Test workspace"
        assert response.created_at == now
        assert response.updated_at == now

    def test_context_search_with_scores(self):
        """Test pattern used in context search endpoints."""

        class ContextWithScore(BaseModel):
            model_config = {"from_attributes": True}

            id: str
            user_id: str
            content: str
            score: float
            created_at: datetime

        now = datetime.now(UTC)
        contexts = [
            MockModel(
                id=uuid4(),
                user_id=uuid4(),
                content="Relevant context",
                created_at=now,
            ),
            MockModel(
                id=uuid4(),
                user_id=uuid4(),
                content="Another context",
                created_at=now,
            ),
        ]
        scores = [0.95, 0.87]

        # This replaces 15+ lines of manual assignment
        items = models_to_schemas(
            ContextWithScore,
            contexts,
            extra_factory=lambda ctx, idx: {"score": scores[idx]},
        )

        assert len(items) == 2
        assert items[0].score == 0.95
        assert items[1].score == 0.87
        assert isinstance(items[0].id, str)
        assert isinstance(items[1].user_id, str)

    def test_list_with_exclusion_pattern(self):
        """Test pattern for excluding sensitive fields."""

        class UserResponse(BaseModel):
            model_config = {"from_attributes": True}

            id: str
            email: str

        users = [
            MockModel(
                id=uuid4(),
                email="user1@example.com",
                password_hash="secret1",
            ),
            MockModel(
                id=uuid4(),
                email="user2@example.com",
                password_hash="secret2",
            ),
        ]

        # Convert to dict first to exclude sensitive fields
        user_dicts = [model_to_dict(user, exclude={"password_hash"}) for user in users]

        # Then validate as schemas
        responses = [UserResponse.model_validate(d) for d in user_dicts]

        assert len(responses) == 2
        assert responses[0].email == "user1@example.com"
        assert not hasattr(responses[0], "password_hash")
