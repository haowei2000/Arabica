"""Document parser that normalizes uploaded files to Markdown."""

import io
import logging
from typing import BinaryIO
import warnings

logger = logging.getLogger(__name__)


class DocumentParser:
    """Convert knowledge documents into Markdown via Microsoft MarkItDown."""

    SUPPORTED_MIME_TYPES = {  # noqa: RUF012
        "text/plain": "txt",
        "text/markdown": "md",
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/vnd.ms-excel": "xls",
        "text/html": "html",
        "text/csv": "csv",
        "application/json": "json",
        "application/xml": "xml",
        "text/xml": "xml",
        "application/zip": "zip",
        "application/epub+zip": "epub",
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/gif": "gif",
        "audio/mpeg": "mp3",
        "audio/wav": "wav",
    }

    SUPPORTED_EXTENSIONS = {  # noqa: RUF012
        ".txt": "txt",
        ".md": "md",
        ".markdown": "md",
        ".pdf": "pdf",
        ".docx": "docx",
        ".pptx": "pptx",
        ".xlsx": "xlsx",
        ".xls": "xls",
        ".html": "html",
        ".htm": "html",
        ".csv": "csv",
        ".json": "json",
        ".xml": "xml",
        ".zip": "zip",
        ".epub": "epub",
        ".png": "png",
        ".jpg": "jpg",
        ".jpeg": "jpg",
        ".gif": "gif",
        ".mp3": "mp3",
        ".wav": "wav",
        ".msg": "msg",
    }

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """Remove null bytes and other invalid characters for PostgreSQL UTF-8.

        Args:
            text: Input text that may contain invalid characters.

        Returns:
            str: Sanitized text safe for PostgreSQL.
        """
        # Remove null bytes (0x00) which are invalid in PostgreSQL
        text = text.replace("\x00", "")
        # Remove other control characters except common whitespace
        sanitized = []
        for char in text:
            code = ord(char)
            # Keep: tab (9), newline (10), carriage return (13), and printable chars (32+)
            if code == 9 or code == 10 or code == 13 or code >= 32:
                sanitized.append(char)
        return "".join(sanitized)

    @staticmethod
    def _read_bytes(data: bytes | BinaryIO) -> bytes:
        if isinstance(data, bytes):
            return data
        data.seek(0)
        return data.read()

    @classmethod
    def _extension_from_filename(cls, filename: str | None) -> str | None:
        if not filename or "." not in filename:
            return None
        return "." + filename.rsplit(".", 1)[-1].lower()

    @classmethod
    def _extension_from_mime_type(cls, mime_type: str | None) -> str | None:
        if not mime_type:
            return None
        file_type = cls.SUPPORTED_MIME_TYPES.get(mime_type.lower())
        return f".{file_type}" if file_type else None

    @staticmethod
    def _markitdown():
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Couldn't find ffmpeg or avconv.*",
                category=RuntimeWarning,
            )
            from markitdown import MarkItDown

        return MarkItDown(enable_plugins=False)

    def parse(
        self,
        data: bytes | BinaryIO,
        mime_type: str | None,
        filename: str | None = None,
    ) -> str:
        """Convert document content to Markdown.

        Args:
            data: Document content as bytes or file-like object.
            mime_type: MIME type of the document.
            filename: Original filename, used to help MarkItDown infer type.

        Returns:
            str: Markdown content.

        Raises:
            ValueError: If MarkItDown cannot convert the file.
        """
        content = self._read_bytes(data)
        extension = self._extension_from_filename(
            filename
        ) or self._extension_from_mime_type(mime_type)

        try:
            result = self._markitdown().convert_stream(
                io.BytesIO(content),
                file_extension=extension,
            )
        except Exception as exc:
            raise ValueError(
                f"Unable to convert document to Markdown: {exc}"
            ) from exc

        markdown = self._sanitize_text(result.text_content or "")
        logger.info(
            "Converted document to Markdown: %s characters%s",
            len(markdown),
            f", extension={extension}" if extension else "",
        )
        return markdown

    def parse_by_extension(self, data: bytes | BinaryIO, filename: str) -> str:
        """Convert document based on file extension.

        Args:
            data: Document content as bytes or file-like object.
            filename: Original filename with extension.

        Returns:
            str: Markdown content.
        """
        return self.parse(data, mime_type=None, filename=filename)

    @classmethod
    def is_supported(cls, mime_type: str) -> bool:
        """Check if MIME type is supported.

        Args:
            mime_type: MIME type to check.

        Returns:
            bool: True if supported.
        """
        return mime_type in cls.SUPPORTED_MIME_TYPES

    @classmethod
    def get_supported_mime_types(cls) -> list[str]:
        """Get list of supported MIME types.

        Returns:
            list[str]: Supported MIME types.
        """
        return list(cls.SUPPORTED_MIME_TYPES.keys())
