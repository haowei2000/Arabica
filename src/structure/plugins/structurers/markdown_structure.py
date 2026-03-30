"""Markdown structurer: heading hierarchy → Linux-style filesystem paths.

Each section in the output carries a ``section_path`` field that encodes
the full heading breadcrumb as a POSIX path, e.g.:

    # Introduction
    ## Getting Started
    ### Installation

produces three sections with paths::

    /Introduction
    /Introduction/Getting Started
    /Introduction/Getting Started/Installation

Rationale
---------
The ``document`` structurer already handles Markdown but only exposes a flat
``level`` integer.  This structurer makes the *structural context* of every
chunk explicit so that retrievers and readers can navigate by path rather
than by reconstructing the tree from level numbers.

Heading detection
-----------------
- ATX:    ``# H1`` … ``###### H6``
- Setext: ``Title\\n======`` (H1)  /  ``Title\\n------`` (H2)

Only ATX and setext headings are recognised; numbered and Chinese-style
headings are treated as plain text (use the ``document`` structurer for those).
"""

from __future__ import annotations

import re

from structure.core.interfaces.structurer import BaseStructurer
from structure.plugins.structurers import register_structurer

# ── Patterns ──────────────────────────────────────────────────────────────────

_ATX = re.compile(r'^(#{1,6})\s+(.+)')
_SETEXT_H1 = re.compile(r'^={3,}\s*$')
_SETEXT_H2 = re.compile(r'^-{3,}\s*$')


def _path_segment(title: str) -> str:
    """Make a title safe to embed in a POSIX path segment.

    Replaces ``/`` (path separator) with the Unicode division slash ``∕``
    (U+2215) so paths remain unambiguous when split on ``/``.
    All other characters are kept as-is — Linux allows them.
    """
    return title.strip().replace("/", "∕")


def _build_path(breadcrumb: list[str]) -> str:
    """Return the POSIX-style path for the given breadcrumb."""
    if not breadcrumb:
        return "/"
    return "/" + "/".join(_path_segment(t) for t in breadcrumb)


def _detect_atx(line: str) -> tuple[int, str] | None:
    m = _ATX.match(line.strip())
    if m:
        return len(m.group(1)), m.group(2).strip()
    return None


def _detect_setext(line: str, next_line: str | None) -> tuple[int, str] | None:
    """Return (level, title) for setext headings, else None."""
    if next_line is None:
        return None
    stripped = line.strip()
    nl = next_line.strip()
    if not stripped or not nl:
        return None
    # Must not look like an ATX heading itself
    if stripped.startswith("#"):
        return None
    if _SETEXT_H1.match(nl):
        return 1, stripped
    if _SETEXT_H2.match(nl):
        return 2, stripped
    return None


@register_structurer
class MarkdownStructurer(BaseStructurer):
    """Chunk Markdown files by ATX/setext heading hierarchy.

    Unlike the generic ``document`` structurer this one:
    * only handles ATX (``#``) and setext (underline) headings,
    * emits a ``section_path`` field (Linux-style path) on every section,
    * preserves preamble text (content before the first heading) as a root
      section at path ``/``.
    """

    name = "markdown"
    description = "Markdown headings → POSIX section_path hierarchy"

    # ── Public interface ───────────────────────────────────────────────────

    def structure(self, text: str, mime_type: str) -> list[dict]:  # noqa: ARG002
        lines = text.splitlines()
        sections: list[dict] = []

        # breadcrumb[i] holds the title of the current heading at level i+1.
        # E.g. for H3 "Install" under H2 "Setup" under H1 "Intro":
        #   breadcrumb == ["Intro", "Setup", "Install"]
        breadcrumb: list[str] = []

        cur_level: int = 1
        cur_title: str = ""
        cur_lines: list[str] = []
        position: int = 0
        found_heading: bool = False

        def flush() -> None:
            nonlocal position
            if not cur_lines and not cur_title:
                return
            content = "\n".join(cur_lines).strip()
            sections.append({
                "title": cur_title,
                "level": cur_level,
                "content": content,
                "position": position,
                "structure_type": "markdown",
                "section_path": _build_path(breadcrumb),
            })
            position += 1

        in_fence = False
        fence_marker = ""

        i = 0
        while i < len(lines):
            line = lines[i]
            next_line = lines[i + 1] if i + 1 < len(lines) else None

            # Track fenced code blocks (``` or ~~~) to avoid treating
            # # lines inside them as headings.
            stripped = line.strip()
            if not in_fence:
                if stripped.startswith("```") or stripped.startswith("~~~"):
                    in_fence = True
                    fence_marker = stripped[:3]
                    cur_lines.append(line)
                    i += 1
                    continue
            else:
                if stripped.startswith(fence_marker):
                    in_fence = False
                cur_lines.append(line)
                i += 1
                continue

            # Setext must be checked before ATX (the underline line would
            # otherwise be picked up as a stray separator).
            setext = _detect_setext(line, next_line)
            if setext is not None:
                level, title = setext
                found_heading = True
                flush()
                cur_level = level
                cur_title = title
                cur_lines = []
                breadcrumb = breadcrumb[:level - 1] + [title]
                i += 2  # consume both the title line and the underline
                continue

            atx = _detect_atx(line)
            if atx is not None:
                level, title = atx
                found_heading = True
                flush()
                cur_level = level
                cur_title = title
                cur_lines = []
                breadcrumb = breadcrumb[:level - 1] + [title]
                i += 1
                continue

            cur_lines.append(line)
            i += 1

        flush()

        # ── Fallback: no Markdown headings found ───────────────────────────
        if not found_heading:
            sections = []
            for idx, para in enumerate(re.split(r"\n{2,}", text)):
                para = para.strip()
                if para:
                    first_line = para.splitlines()[0][:120]
                    sections.append({
                        "title": first_line,
                        "level": 1,
                        "content": para,
                        "position": idx,
                        "structure_type": "markdown",
                        "section_path": "/" + _path_segment(first_line),
                    })

        # ── Ultimate fallback: empty document ─────────────────────────────
        if not sections:
            sections.append({
                "title": "",
                "level": 1,
                "content": text.strip(),
                "position": 0,
                "structure_type": "markdown",
                "section_path": "/",
            })

        return sections
