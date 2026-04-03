from abc import ABC, abstractmethod
from typing import Any

from structure.schemas.context.context_schema import ContextCore


class BaseStructurer(ABC):
    name: str
    description: str

    @abstractmethod
    def structure(self, input: Any) -> list[ContextCore]:
        """Return a list of section dicts with keys: title, level, content, position, structure_type, ..."""
