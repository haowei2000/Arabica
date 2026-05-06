"""Unit tests for uploaded document context structuring."""

import pytest

from structure.celery_worker.tasks.context_sync import sync_document


@pytest.mark.unit
def test_document_markdown_structure_emits_heading_tree_contexts():
    cores = sync_document._structure_document_markdown(
        "# Overview\n\nIntro text.\n\n## Setup\n\nInstall steps.",
        mime_type="text/markdown",
        original_name="Guide.md",
        knowledge_name="Product Docs",
        conversion_source="markdown",
    )

    assert [core["path"] for core in cores] == [
        "/knowledge/product-docs/documents/guide.md/overview",
        "/knowledge/product-docs/documents/guide.md/overview/setup",
    ]
    assert cores[0]["glance"] == "Overview"
    assert cores[0]["meta"]["conversion_source"] == "markdown"
    assert cores[1]["meta"]["section_path"] == "/Overview/Setup"


@pytest.mark.unit
def test_document_markdown_structure_splits_headingless_text_into_paragraphs():
    cores = sync_document._structure_document_markdown(
        "First paragraph has useful text.\n\nSecond paragraph has more.",
        mime_type="text/markdown",
        original_name="notes.txt",
        knowledge_name="Ops KB",
        conversion_source="markitdown",
    )

    assert [core["path"] for core in cores] == [
        "/knowledge/ops-kb/documents/notes.txt/first-paragraph-has-useful-text",
        "/knowledge/ops-kb/documents/notes.txt/second-paragraph-has-more",
    ]
    assert [core["meta"]["conversion_source"] for core in cores] == [
        "markitdown",
        "markitdown",
    ]


@pytest.mark.unit
def test_markdown_upload_uses_direct_markdown_parser(monkeypatch):
    def fail_convert(*_args, **_kwargs) -> str:
        raise AssertionError("Markdown uploads should not call MarkItDown")

    monkeypatch.setattr(sync_document, "_convert_with_markitdown", fail_convert)

    markdown, source = sync_document._document_to_markdown(
        b"# Direct\n\nBody.",
        "text/markdown",
        "Direct.md",
    )

    assert markdown == "# Direct\n\nBody."
    assert source == "markdown"


@pytest.mark.unit
def test_non_markdown_document_uses_markitdown_conversion(monkeypatch):
    def fake_convert(file_data: bytes, mime_type: str, original_name: str) -> str:
        assert file_data == b"pdf bytes"
        assert mime_type == "application/pdf"
        assert original_name == "Report.pdf"
        return "# Converted\n\nStructured body."

    monkeypatch.setattr(sync_document, "_convert_with_markitdown", fake_convert)

    markdown, source = sync_document._document_to_markdown(
        b"pdf bytes",
        "application/pdf",
        "Report.pdf",
    )

    assert markdown == "# Converted\n\nStructured body."
    assert source == "markitdown"
