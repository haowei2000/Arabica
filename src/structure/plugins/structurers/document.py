"""Robust multi-level document structurer.

Recognises headings in:
- Markdown ATX:     # H1 … ###### H6
- Markdown setext:  Title\n======  (H1)   Title\n------  (H2)
- Numbered:         1.   /  1.1   /  1.1.1
- Chinese chapters: 第一章, 第二节, 第三篇 …
- Chinese ordinals: 一、  /  （一）  /  ①②③ …
- ALL-CAPS short lines (English only, as H2 fallback)

Output is a *flat* list of section dicts.  Each dict carries a ``level``
(1-6) so the frontend ``buildSectionTree`` can reconstruct the tree without
any additional processing.
"""
import re

from structure.core.interfaces.structurer import BaseStructurer
from structure.plugins.structurers import register_structurer

# ── Chinese numeral sets ──────────────────────────────────────────────────────
_ZH_NUM = "一二三四五六七八九十百千零"
_CIRCLE_NUMS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"

# ── Pre-compiled heading patterns ────────────────────────────────────────────
# Each entry: (compiled_regex, level_resolver, title_resolver)
# level_resolver(m) -> int
# title_resolver(m) -> str
_PATTERNS: list[tuple[re.Pattern, object, object]] = [

    # 1. Markdown ATX  # Title … ###### Title
    (re.compile(r'^(#{1,6})\s+(.+)'),
     lambda m: len(m.group(1)),
     lambda m: m.group(2).strip()),

    # 2. Numbered 3-level  1.2.3  or  1.2.3.
    (re.compile(r'^\d+\.\d+\.\d+\.?\s+(.{1,100})$'),
     lambda m: 3,
     lambda m: m.group(1).strip()),

    # 3. Numbered 2-level  1.2  or  1.2.
    (re.compile(r'^\d+\.\d+\.?\s+(.{1,100})$'),
     lambda m: 2,
     lambda m: m.group(1).strip()),

    # 4. Numbered 1-level  1.  (keep line short to avoid matching sentences)
    (re.compile(r'^\d+\.\s+(.{1,80})$'),
     lambda m: 1,
     lambda m: m.group(1).strip()),

    # 5. Chinese chapter/section  第X章 Title  第X节  第X篇  第X部  第X编
    (re.compile(rf'^(第[{_ZH_NUM}\d]+[章节篇部编])\s*(.{{0,80}})$'),
     lambda m: 1,
     lambda m: (m.group(1) + (" " + m.group(2).strip() if m.group(2).strip() else "")).strip()),

    # 6. Chinese ordinal  一、 Title  二、 Title
    (re.compile(rf'^([{_ZH_NUM}]+[、．.。])\s*(.{{1,80}})$'),
     lambda m: 2,
     lambda m: (m.group(1) + " " + m.group(2).strip()).strip()),

    # 7. Chinese parenthesised  （一）Title  (1)Title
    (re.compile(rf'^([（(][{_ZH_NUM}\d]+[）)])\s*(.{{1,80}})$'),
     lambda m: 3,
     lambda m: (m.group(1) + " " + m.group(2).strip()).strip()),

    # 8. Circled numbers  ① Title … ⑳ Title
    (re.compile(rf'^([{_CIRCLE_NUMS}])\s*(.{{1,80}})$'),
     lambda m: 4,
     lambda m: (m.group(1) + " " + m.group(2).strip()).strip()),
]

# Setext-style underlines
_SETEXT_H1 = re.compile(r'^={3,}\s*$')
_SETEXT_H2 = re.compile(r'^-{3,}\s*$')

# ALL-CAPS short line (English headings without markup)
_ALL_CAPS = re.compile(r'^[A-Z][A-Z0-9 \t\-–—:,./]{2,60}$')


def _detect_heading(line: str, next_line: str | None) -> tuple[int, str] | None:
    """Return (level, title) if *line* is a heading, else None.

    ``next_line`` is needed only for setext-style detection.
    """
    stripped = line.strip()
    if not stripped:
        return None

    # Setext underline headings — must be checked before ATX since "---"
    # itself would otherwise be treated as an HR / separator
    if next_line is not None:
        nl = next_line.strip()
        if nl and _SETEXT_H1.match(nl):
            return 1, stripped
        if nl and _SETEXT_H2.match(nl):
            return 2, stripped

    # Named pattern-list
    for pattern, lvl_fn, title_fn in _PATTERNS:
        m = pattern.match(stripped)
        if m:
            return lvl_fn(m), title_fn(m)  # type: ignore[operator]

    # ALL-CAPS short English line (treat as H2 — lower priority fallback)
    if _ALL_CAPS.match(stripped) and len(stripped) <= 64:
        return 2, stripped

    return None


def _flush(title: str, level: int, lines: list[str], pos: int) -> dict:
    content = "\n".join(lines).strip()
    # If there's no explicit title, derive one from the first content line
    if not title and content:
        title = content.splitlines()[0][:120]
    return {
        "title": title,
        "level": level,
        "content": content,
        "position": pos,
        "structure_type": "document",
    }


@register_structurer
class DocumentStructurer(BaseStructurer):
    name = "document"
    description = "Multi-level document structuring (Markdown, numbered, Chinese, setext)"

    def structure(self, text: str, mime_type: str) -> list[dict]:
        lines = text.splitlines()
        sections: list[dict] = []

        cur_level: int = 1
        cur_title: str = ""
        cur_lines: list[str] = []
        pos: int = 0
        found_any_heading = False

        i = 0
        while i < len(lines):
            line = lines[i]
            next_line = lines[i + 1] if i + 1 < len(lines) else None

            result = _detect_heading(line, next_line)

            if result is not None:
                level, title = result
                found_any_heading = True

                # Setext headings consume the underline line — skip it.
                # Detected when: next line is all = or -, title equals the raw
                # line (setext sets title == line.strip()), and the line itself
                # is not ATX-style (no leading #).
                is_setext = (
                    next_line is not None
                    and bool(_SETEXT_H1.match(next_line.strip() or "a")
                             or _SETEXT_H2.match(next_line.strip() or "a"))
                    and title == line.strip()
                    and not re.match(r'^#{1,6}\s', line)
                )

                # Flush current accumulator
                if cur_lines or cur_title:
                    sections.append(_flush(cur_title, cur_level, cur_lines, pos))
                    pos += 1

                cur_level = level
                cur_title = title
                cur_lines = []
                i += 2 if is_setext else 1
            else:
                cur_lines.append(line)
                i += 1

        # Flush final section
        if cur_lines or cur_title:
            sections.append(_flush(cur_title, cur_level, cur_lines, pos))

        # ── Fallback: no headings found → split by blank lines ────────────
        if not found_any_heading:
            sections = []
            for i, para in enumerate(re.split(r"\n{2,}", text)):
                para = para.strip()
                if para:
                    first_line = para.splitlines()[0][:120]
                    sections.append({
                        "title": first_line,
                        "level": 1,
                        "content": para,
                        "position": i,
                        "structure_type": "document",
                    })

        if not sections:
            sections.append({
                "title": "",
                "level": 1,
                "content": text.strip(),
                "position": 0,
                "structure_type": "document",
            })

        return sections
