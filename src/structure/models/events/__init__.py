"""Event domain models - event sourcing."""

from structure.core.enums import EventType
from structure.models.events.event import Event

__all__ = [
    "Event",
    "EventType",
]
