"""Text chunking service for document processing."""

from dataclasses import dataclass
import logging
import re

logger = logging.getLogger(__name__)


@dataclass
class ChunkConfig:
    """Configuration for text chunking."""

    chunk_size: int = 500
    chunk_overlap: int = 50
    separator: str = "\n\n"
    fallback_separators: list[str] | None = None

    def __post_init__(self) -> None:
        if self.fallback_separators is None:
            self.fallback_separators = ["\n", ". ", " "]


@dataclass
class TextChunk:
    """Represents a chunk of text with metadata."""

    content: str
    position: int
    start_char: int
    end_char: int

    @property
    def length(self) -> int:
        return len(self.content)


class TextChunker:
    """Split text into chunks with configurable size and overlap."""

    def __init__(self, config: ChunkConfig | None = None) -> None:
        """Initialize chunker with configuration.

        Args:
            config: Chunking configuration. Uses defaults if None.
        """
        self.config = config or ChunkConfig()

    def chunk(self, text: str) -> list[TextChunk]:
        """Split text into chunks.

        Args:
            text: Text to split into chunks.

        Returns:
            list[TextChunk]: List of text chunks with metadata.
        """
        if not text or not text.strip():
            return []

        # Clean the text
        text = self._clean_text(text)

        # Split by primary separator first
        segments = self._split_by_separator(text, self.config.separator)

        # Combine or split segments to match chunk size
        chunks = self._process_segments(segments, text)

        logger.info(
            f"Created {len(chunks)} chunks from {len(text)} characters "
            f"(size={self.config.chunk_size}, overlap={self.config.chunk_overlap})"
        )

        return chunks

    def _clean_text(self, text: str) -> str:
        """Clean and normalize text.

        Args:
            text: Raw text to clean.

        Returns:
            str: Cleaned text.
        """
        # Remove excessive whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}", " ", text)
        return text.strip()

    def _split_by_separator(self, text: str, separator: str) -> list[str]:
        """Split text by separator.

        Args:
            text: Text to split.
            separator: Separator string.

        Returns:
            list[str]: List of text segments.
        """
        if separator:  # noqa: SIM108
            segments = text.split(separator)
        else:
            segments = [text]

        return [seg.strip() for seg in segments if seg.strip()]

    def _process_segments(
        self, segments: list[str], original_text: str
    ) -> list[TextChunk]:
        """Process segments into properly sized chunks.

        Args:
            segments: List of text segments.
            original_text: Original full text for position tracking.

        Returns:
            list[TextChunk]: List of text chunks.
        """
        chunks = []
        current_chunk = ""
        position = 0
        search_start = 0

        for segment in segments:
            # If segment alone exceeds chunk size, split it further
            if len(segment) > self.config.chunk_size:
                # First, save any accumulated content
                if current_chunk:
                    chunk_start = original_text.find(current_chunk, search_start)
                    if chunk_start == -1:
                        chunk_start = search_start
                    chunks.append(
                        TextChunk(
                            content=current_chunk,
                            position=position,
                            start_char=chunk_start,
                            end_char=chunk_start + len(current_chunk),
                        )
                    )
                    position += 1
                    search_start = (
                        chunk_start + len(current_chunk) - self.config.chunk_overlap
                    )
                    current_chunk = ""

                # Split large segment
                sub_chunks = self._split_large_segment(segment)
                for sub in sub_chunks:
                    chunk_start = original_text.find(sub, max(0, search_start))
                    if chunk_start == -1:
                        chunk_start = search_start
                    chunks.append(
                        TextChunk(
                            content=sub,
                            position=position,
                            start_char=chunk_start,
                            end_char=chunk_start + len(sub),
                        )
                    )
                    position += 1
                    search_start = chunk_start + len(sub) - self.config.chunk_overlap
                continue

            # Check if adding segment exceeds chunk size
            test_chunk = (
                f"{current_chunk}{self.config.separator}{segment}"
                if current_chunk
                else segment
            )

            if len(test_chunk) <= self.config.chunk_size:
                current_chunk = test_chunk
            else:
                # Save current chunk and start new one
                if current_chunk:
                    chunk_start = original_text.find(current_chunk, search_start)
                    if chunk_start == -1:
                        chunk_start = search_start
                    chunks.append(
                        TextChunk(
                            content=current_chunk,
                            position=position,
                            start_char=chunk_start,
                            end_char=chunk_start + len(current_chunk),
                        )
                    )
                    position += 1
                    search_start = (
                        chunk_start + len(current_chunk) - self.config.chunk_overlap
                    )

                # Add overlap from previous chunk if configured
                if self.config.chunk_overlap > 0 and chunks:
                    overlap_text = self._get_overlap_text(chunks[-1].content)
                    current_chunk = f"{overlap_text} {segment}".strip()
                else:
                    current_chunk = segment

        # Don't forget the last chunk
        if current_chunk:
            chunk_start = original_text.find(current_chunk, search_start)
            if chunk_start == -1:
                chunk_start = search_start
            chunks.append(
                TextChunk(
                    content=current_chunk,
                    position=position,
                    start_char=chunk_start,
                    end_char=chunk_start + len(current_chunk),
                )
            )

        return chunks

    def _split_large_segment(self, segment: str) -> list[str]:
        """Split a large segment using fallback separators.

        Args:
            segment: Large text segment to split.

        Returns:
            list[str]: List of smaller chunks.
        """
        chunks = []
        remaining = segment

        for separator in self.config.fallback_separators or []:
            if len(remaining) <= self.config.chunk_size:
                break

            parts = remaining.split(separator)
            current = ""

            for part in parts:
                test = f"{current}{separator}{part}" if current else part

                if len(test) <= self.config.chunk_size:
                    current = test
                else:
                    if current:
                        chunks.append(current.strip())
                        # Add overlap
                        overlap = self._get_overlap_text(current)
                        current = f"{overlap} {part}".strip() if overlap else part
                    else:
                        # Part itself is too large, will be handled by next separator
                        current = part

            remaining = current

        # Add any remaining content
        if remaining:
            # If still too large, force split by character count
            while len(remaining) > self.config.chunk_size:
                # Try to find a good break point
                break_point = self.config.chunk_size
                for char in [" ", ",", ".", ";"]:
                    last_pos = remaining[: self.config.chunk_size].rfind(char)
                    if last_pos > self.config.chunk_size * 0.5:
                        break_point = last_pos + 1
                        break

                chunks.append(remaining[:break_point].strip())
                overlap_start = max(0, break_point - self.config.chunk_overlap)
                remaining = remaining[overlap_start:].strip()

            if remaining:
                chunks.append(remaining.strip())

        return chunks

    def _get_overlap_text(self, text: str) -> str:
        """Get overlap text from the end of a chunk.

        Args:
            text: Text to get overlap from.

        Returns:
            str: Overlap text.
        """
        if len(text) <= self.config.chunk_overlap:
            return text

        overlap = text[-self.config.chunk_overlap :]

        # Try to start at a word boundary
        space_pos = overlap.find(" ")
        if space_pos > 0:
            return overlap[space_pos + 1 :]

        return overlap
