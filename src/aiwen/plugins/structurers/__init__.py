from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiwen.core.interfaces.structurer import BaseStructurer

_REGISTRY: dict[str, BaseStructurer] = {}


def register_structurer(cls):
    """Class decorator: instantiate and register a BaseStructurer subclass."""
    instance = cls()
    _REGISTRY[instance.name] = instance
    return cls


def get_structurer(name: str) -> BaseStructurer:
    """Return the named structurer; raises KeyError if not registered."""
    return _REGISTRY[name]


def dispatch_structure(text: str, mime_type: str, structure_type: str) -> list[dict]:
    """Route to the matching structurer; falls back to 'document' if unknown."""
    structurer = _REGISTRY.get(structure_type) or _REGISTRY["document"]
    return structurer.structure(text, mime_type)


# Auto-register built-in plugins (import order matters: document first so
# table/code can reference DocumentStructurer in their fallback paths)
from . import code, document, table  # noqa: E402
