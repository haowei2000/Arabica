"""Custom SQLAlchemy type definitions for model fields."""

from sqlalchemy import String, Text, TypeDecorator


class StringUUID(TypeDecorator):
    """String-based UUID column type (stores UUID as 36-character string)."""

    impl = String(36)
    cache_ok = True


class LongText(TypeDecorator):
    """Long text column type for storing large text content."""

    impl = Text
    cache_ok = True
