"""File-related enum definitions."""

from enum import Enum


class InputFormat(str, Enum):
    """Supported input formats."""

    markdown = "md"
    text = "txt"


class OutputFormat(str, Enum):
    """Supported output formats."""

    word = "word"
    pdf = "pdf"
