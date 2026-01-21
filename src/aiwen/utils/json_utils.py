"""JSON serialization utilities with support for UUID, datetime, and other types."""
from datetime import date, datetime
from decimal import Decimal
import json
from typing import Any
from uuid import UUID


def json_default(obj: Any) -> Any:
    """
    JSON serializer for objects not serializable by default json code.

    Handles:
    - UUID objects -> str
    - datetime/date objects -> ISO format string
    - Decimal objects -> float

    Args:
        obj: Object to serialize

    Returns:
        Serializable representation of the object

    Raises:
        TypeError: If object type is not supported
    """
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def dumps(obj: Any, **kwargs) -> str:
    """
    Serialize obj to a JSON formatted string with support for UUID, datetime, etc.

    Args:
        obj: Object to serialize
        **kwargs: Additional arguments passed to json.dumps

    Returns:
        JSON formatted string
    """
    return json.dumps(obj, default=json_default, **kwargs)


def safe_dumps(obj: Any, **kwargs) -> str:
    """
    Safely serialize obj to JSON, falling back to string representation on error.

    Args:
        obj: Object to serialize
        **kwargs: Additional arguments passed to json.dumps

    Returns:
        JSON formatted string or error input
    """
    try:
        return dumps(obj, **kwargs)
    except Exception as e:
        return json.dumps({"error": f"Serialization failed: {e!s}"})
