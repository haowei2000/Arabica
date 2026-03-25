import re


def slugify(name: str) -> str:
    """Convert a human-readable name to a safe path segment (lowercase, underscores)."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s\-]+", "_", slug)
    return slug or "unnamed"