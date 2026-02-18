from abc import ABC, abstractmethod


class BaseStructurer(ABC):
    name: str
    description: str

    @abstractmethod
    def structure(self, text: str, mime_type: str) -> list[dict]:
        """Return a list of section dicts with keys: title, level, content, position, structure_type, ..."""
