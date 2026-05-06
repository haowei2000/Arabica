import re

_ROOT_ALIASES = {
    "tool": "tools",
    "tools": "tools",
    "skill": "skills",
    "skills": "skills",
    "knowledge": "knowledge",
    "memory": "memory",
    "memories": "memory",
    "short-memory": "memory",
    "short_memory": "memory",
    "long-memory": "memory",
    "long_memory": "memory",
    "workspace": "workspaces",
    "workspaces": "workspaces",
    "run": "runs",
    "runs": "runs",
    "trigger": "triggers",
    "triggers": "triggers",
}


def normalize_context_path(path: str | None) -> str | None:
    """Return the canonical context path stored in the DB.

    Agent-facing APIs accept both ``tools/x`` and ``/tools/x`` for compatibility.
    Persisted paths use a leading slash and semantic root aliases such as
    ``tool`` → ``tools`` and ``skill`` → ``skills``.
    """
    if path is None:
        return None

    stripped = path.strip()
    if not stripped or stripped == "/":
        return "/"

    segments = [segment for segment in stripped.strip("/").split("/") if segment]
    if segments:
        root = segments[0].strip().lower()
        segments[0] = _ROOT_ALIASES.get(root, root)
    return "/" + "/".join(segments)


def slugify(name: str) -> str:
    """Slugify a single path segment (no slashes — use build_path for full paths)."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s\-]+", "_", slug)
    return slug or "unnamed"


def semantic_slugify(name: str, *, preserve_extension: bool = True) -> str:
    """Slugify a path segment while keeping human-readable semantics.

    Unlike ``slugify``, this prefers hyphens, treats underscores as word
    separators, and keeps file extensions such as ``README.md`` readable.
    """
    raw = str(name).strip()
    if not raw:
        return "unnamed"

    stem = raw
    suffix = ""
    if preserve_extension:
        match = re.match(r"^(.+?)(\.[A-Za-z0-9]{1,12})$", raw)
        if match:
            stem, suffix = match.group(1), match.group(2).lower()

    slug = stem.lower().strip()
    slug = re.sub(r"[_\s-]+", "-", slug)
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "", slug)
    slug = slug.strip("-_")
    return f"{slug or 'unnamed'}{suffix}"


def build_context_path(root: str, *parts: str) -> str:
    """Build a canonical, semantic Context path.

    The first segment is a domain root (``tools``, ``skills``, ``knowledge``,
    ``memory``, ``workspaces``, ``runs``). Remaining segments are readable
    slugs derived from display names or natural labels.
    """
    raw_segments: list[str] = []
    for part in (root, *parts):
        raw_segments.extend(
            seg for seg in re.split(r"/+", str(part).strip("/")) if seg
        )
    if not raw_segments:
        return "/"

    root_slug = semantic_slugify(raw_segments[0], preserve_extension=False)
    segments = [_ROOT_ALIASES.get(root_slug, root_slug)]
    for seg in raw_segments[1:]:
        slug = semantic_slugify(seg)
        if slug and slug != "unnamed":
            segments.append(slug)
    return normalize_context_path("/".join(segments)) or "/"


def semantic_context_path(path: str | None) -> str | None:
    """Return the semantic canonical form of an existing context path."""
    normalized = normalize_context_path(path)
    if normalized is None:
        return None
    segments = [segment for segment in normalized.strip("/").split("/") if segment]
    if not segments:
        return "/"
    root, *rest = segments
    return build_context_path(root, *rest)


def context_path_variants(path: str | None) -> list[str]:
    """Return compatibility variants for matching old and semantic paths."""
    normalized = normalize_context_path(path)
    if normalized is None:
        return []

    variants = [normalized]
    semantic = semantic_context_path(normalized)
    if semantic and semantic not in variants:
        variants.append(semantic)

    for value in list(variants):
        legacy = value.strip("/")
        if legacy and legacy not in variants:
            variants.append(legacy)
    return variants


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
        → "/knowledge/my-kb/documents/guide.md"

        build_path("knowledge", "My KB", "documents", "README.md",
                   "/Introduction/Getting Started")
        → "/knowledge/my-kb/documents/readme.md/introduction/getting-started"
    """
    if not parts:
        return "/"
    raw_segments: list[str] = []
    for part in parts:
        raw_segments.extend(
            seg for seg in re.split(r"/+", str(part).strip("/")) if seg
        )
    if not raw_segments:
        return "/"
    root, *rest = raw_segments
    return build_context_path(root, *rest)
