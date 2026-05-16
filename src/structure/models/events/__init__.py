"""Event domain models - event sourcing."""

from structure.core.enums.events import EventType
from structure.models.events.event import Event

__all__ = [
    "Event",
    "EventType",
]
