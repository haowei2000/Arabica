"""Utilities for converting SQLAlchemy models to Pydantic schemas.

This module provides functions to eliminate manual field-by-field assignments
when converting ORM models to response schemas, particularly in list endpoints.

Usage Examples:
    # Convert single model with extra fields
    response = model_to_schema(
        ContextWithScore,
        context,
        extra={"score": 0.95}
    )

    # Convert list of models
    items = models_to_schemas(RunResponse, runs)

    # Convert with field exclusion
    response = model_to_schema(
        UserResponse,
        user,
        exclude={"password_hash", "internal_id"}
    )

    # Convert list with dynamic extra fields
    items = models_to_schemas(
        ContextWithScore,
        contexts,
        extra_factory=lambda ctx, idx: {"score": scores[idx]}
    )
"""

from collections.abc import Callable
from enum import Enum
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def normalize_uuid(value: UUID | str | None) -> str | None:
    """Normalize UUID value to string format.

    Args:
        value: UUID object, string, or None

    Returns:
        String representation of UUID, or None if input is None

    Example:
        >>> from uuid import uuid4
        >>> uuid_obj = uuid4()
        >>> normalize_uuid(uuid_obj)
        '123e4567-e89b-12d3-a456-426614174000'
        >>> normalize_uuid("123e4567-e89b-12d3-a456-426614174000")
        '123e4567-e89b-12d3-a456-426614174000'
        >>> normalize_uuid(None)
        None
    """
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    return value


def extract_enum_value(value: Enum | Any) -> Any:
    """Extract .value from Enum or return value as-is.

    Args:
        value: Enum instance or any other value

    Returns:
        Enum.value if value is an Enum, otherwise the original value

    Example:
        >>> from enum import Enum
        >>> class Status(str, Enum):
        ...     ACTIVE = "active"
        >>> extract_enum_value(Status.ACTIVE)
        'active'
        >>> extract_enum_value("plain_string")
        'plain_string'
    """
    if isinstance(value, Enum):
        return value.value
    return value


def extract_enum_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Extract enum values from all fields in a dictionary.

    Args:
        data: Dictionary that may contain Enum values

    Returns:
        Dictionary with all Enum values converted to their .value

    Example:
        >>> from enum import Enum
        >>> class Status(str, Enum):
        ...     ACTIVE = "active"
        >>> extract_enum_dict({"status": Status.ACTIVE, "name": "test"})
        {'status': 'active', 'name': 'test'}
    """
    return {key: extract_enum_value(val) for key, val in data.items()}


def convert_uuid_fields(obj: Any) -> dict[str, Any]:
    """Extract dictionary from ORM model, converting UUIDs to strings.

    This function handles the common pattern of converting SQLAlchemy models
    to dictionaries while ensuring UUID fields are serialized as strings.

    Args:
        obj: SQLAlchemy model instance

    Returns:
        Dictionary with UUID fields converted to strings

    Example:
        >>> from uuid import uuid4
        >>> class User:
        ...     id = uuid4()
        ...     name = "Alice"
        >>> data = convert_uuid_fields(User())
        >>> isinstance(data["id"], str)
        True
    """
    if not hasattr(obj, "__dict__"):
        return {}

    result = {}
    for key, value in obj.__dict__.items():
        if key.startswith("_"):
            continue
        if isinstance(value, UUID):
            result[key] = str(value)
        elif isinstance(value, Enum):
            result[key] = value.value
        else:
            result[key] = value
    return result


def model_to_schema[
    T: BaseModel
](
    schema_class: type[T],
    model_instance: Any,
    exclude: set[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> T:
    """Convert SQLAlchemy model to Pydantic schema.

    This function replaces 15-line manual field assignments in routers with
    a single function call, supporting:
    - Automatic UUID to string conversion
    - Automatic Enum to value conversion
    - Field exclusion (e.g., sensitive fields)
    - Adding computed/extra fields (e.g., scores, counts)

    Args:
        schema_class: Pydantic schema class to instantiate
        model_instance: SQLAlchemy model instance
        exclude: Set of field names to exclude from conversion
        extra: Additional fields to add (e.g., computed values)

    Returns:
        Instance of schema_class populated with model data

    Example:
        >>> # Instead of 15 lines of manual assignment:
        >>> # response = ContextWithScore(
        >>> #     id=str(context.id),
        >>> #     user_id=str(context.user_id),
        >>> #     ... 10 more fields ...
        >>> #     score=score
        >>> # )
        >>> # Use one line:
        >>> response = model_to_schema(
        ...     ContextWithScore,
        ...     context,
        ...     extra={"score": 0.95}
        ... )

    Raises:
        ValidationError: If the model data doesn't match the schema
    """
    data = convert_uuid_fields(model_instance)

    if exclude:
        data = {k: v for k, v in data.items() if k not in exclude}

    if extra:
        data.update(extra)

    return schema_class.model_validate(data)


def models_to_schemas[
    T: BaseModel
](
    schema_class: type[T],
    model_instances: list[Any],
    exclude: set[str] | None = None,
    extra_factory: Callable[[Any, int], dict[str, Any]] | None = None,
) -> list[T]:
    """Convert list of SQLAlchemy models to Pydantic schemas.

    This function replaces list comprehensions with 15-line manual assignments
    with a simple function call.

    Args:
        schema_class: Pydantic schema class to instantiate
        model_instances: List of SQLAlchemy model instances
        exclude: Set of field names to exclude from conversion
        extra_factory: Optional function to generate extra fields per instance.
                      Takes (model_instance, index) and returns dict of extra fields.

    Returns:
        List of schema instances

    Example:
        >>> # Simple list conversion
        >>> runs = models_to_schemas(RunResponse, run_models)

        >>> # With dynamic extra fields (e.g., from parallel list of scores)
        >>> contexts = models_to_schemas(
        ...     ContextWithScore,
        ...     context_models,
        ...     extra_factory=lambda ctx, idx: {"score": scores[idx]}
        ... )

        >>> # With field exclusion
        >>> users = models_to_schemas(
        ...     UserResponse,
        ...     user_models,
        ...     exclude={"password_hash"}
        ... )
    """
    result = []
    for idx, instance in enumerate(model_instances):
        extra = extra_factory(instance, idx) if extra_factory else None
        result.append(model_to_schema(schema_class, instance, exclude=exclude, extra=extra))
    return result


def model_to_dict(
    model_instance: Any,
    exclude: set[str] | None = None,
    include: set[str] | None = None,
) -> dict[str, Any]:
    """Convert SQLAlchemy model to dictionary with UUID/Enum conversion.

    Similar to model_to_schema but returns a plain dict instead of a Pydantic model.
    Useful for JSON responses or custom serialization.

    Args:
        model_instance: SQLAlchemy model instance
        exclude: Set of field names to exclude
        include: Set of field names to include (if None, include all)

    Returns:
        Dictionary with converted values

    Example:
        >>> data = model_to_dict(user, exclude={"password_hash"})
        >>> json.dumps(data)  # All UUIDs already strings
    """
    data = convert_uuid_fields(model_instance)

    if include:
        data = {k: v for k, v in data.items() if k in include}
    if exclude:
        data = {k: v for k, v in data.items() if k not in exclude}

    return data
