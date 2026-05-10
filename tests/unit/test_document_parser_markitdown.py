from io import BytesIO

import pytest

from structure.services.context.knowledge.parser import DocumentParser


def test_document_parser_converts_csv_to_markdown_table():
    parser = DocumentParser()

    markdown = parser.parse(
        b"name,value\nalpha,1\n",
        "text/csv",
        filename="sample.csv",
    )

    assert "| name | value |" in markdown
    assert "| alpha | 1 |" in markdown


def test_document_parser_preserves_markdown_headings_from_stream():
    parser = DocumentParser()

    markdown = parser.parse(
        BytesIO(b"# Title\n\nBody"),
        "text/markdown",
        filename="sample.md",
    )

    assert markdown.strip() == "# Title\n\nBody"


def test_document_parser_wraps_markitdown_errors():
    parser = DocumentParser()

    class FailingMarkItDown:
        def convert_stream(self, *args, **kwargs):
            raise RuntimeError("conversion failed")

    parser._markitdown = lambda: FailingMarkItDown()

    with pytest.raises(ValueError, match="Unable to convert document to Markdown"):
        parser.parse(b"content", "application/octet-stream", filename="sample.bin")
