import re


def slugify(name: str) -> str:
    """Slugify a single path segment (no slashes — use build_path for full paths)."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s\-]+", "_", slug)
    return slug or "unnamed"


def build_path(*parts: str) -> str:
    """Build a hierarchical context path with / separators.

    Each *part* is split on '/' first so that sub-paths (e.g. a
    MarkdownStructurer section_path like '/Introduction/Getting Started')
    are expanded into individual segments.  Every segment is then slugified
    independently and the results are rejoined with '/'.

    This avoids the classic pitfall of calling slugify() on an entire path
    string, which strips every '/' and collapses all segments into one blob.

    Examples::

        build_path("knowledge", "My KB", "documents", "guide.md")
        → "/knowledge/my_kb/documents/guidemd"

        build_path("knowledge", "My KB", "documents", "README.md",
                   "/Introduction/Getting Started")
        → "/knowledge/my_kb/documents/readmemd/introduction/getting_started"
    """
    segments: list[str] = []
    for part in parts:
        for seg in re.split(r"/+", part.strip("/")):
            slug = slugify(seg)
            if slug and slug != "unnamed":
                segments.append(slug)
    return "/" + "/".join(segments) if segments else "/"
