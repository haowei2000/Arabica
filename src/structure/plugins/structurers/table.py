import re

from structure.core.interfaces.structurer import BaseStructurer
from structure.plugins.structurers import register_structurer


def _row_section(headers: list[str], cells: list[str], position: int, row_index: int) -> dict:
    """Build a single header+row section dict."""
    header_line = " | ".join(headers)
    data_line = " | ".join(cells[:len(headers)])
    content = f"{header_line}\n{data_line}"
    title = next((c[:80] for c in cells if c.strip()), f"Row {row_index + 1}")
    return {
        "title": title,
        "level": 1,
        "content": content,
        "position": position,
        "structure_type": "table",
        "column_names": headers,
        "row_index": row_index,
    }


@register_structurer
class TableStructurer(BaseStructurer):
    name = "table"
    description = "Table-row extraction structuring (one section per header+row pair)"

    def structure(self, text: str, mime_type: str) -> list[dict]:
        from structure.plugins.structurers.document import DocumentStructurer
        _doc = DocumentStructurer()

        sections: list[dict] = []

        # ── CSV ──────────────────────────────────────────────────────────────
        if mime_type == "text/csv":
            import csv
            import io
            reader = csv.reader(io.StringIO(text))
            rows = [row for row in reader if any(c.strip() for c in row)]
            if len(rows) >= 2:
                headers = [h.strip() for h in rows[0]]
                for i, cells in enumerate(rows[1:]):
                    sections.append(_row_section(headers, [c.strip() for c in cells], len(sections), i))
            return sections or _doc.structure(text, mime_type)

        # ── Markdown / plain text: pipe tables ────────────────────────────
        if mime_type in ("text/markdown", "text/plain"):
            lines = text.splitlines()
            i = 0
            while i < len(lines):
                line = lines[i]
                if re.match(r"^\s*\|.+\|", line) or ("|" in line and line.strip().startswith("|")):
                    headers = [h.strip() for h in line.strip().strip("|").split("|") if h.strip()]
                    j = i + 1
                    # skip separator row (--- | --- | ---)
                    if j < len(lines) and re.match(r"^\s*\|[-: |]+\|", lines[j]):
                        j += 1
                    row_i = 0
                    while j < len(lines) and (re.match(r"^\s*\|.+\|", lines[j]) or "|" in lines[j]):
                        cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                        if any(c.strip() for c in cells):
                            sections.append(_row_section(headers, cells, len(sections), row_i))
                        row_i += 1
                        j += 1
                    i = j
                    continue
                i += 1
            return sections or _doc.structure(text, mime_type)

        # ── HTML: <table> elements ────────────────────────────────────────
        if mime_type == "text/html":
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(text, "lxml")
                for table in soup.find_all("table"):
                    rows = table.find_all("tr")
                    if not rows:
                        continue
                    header_cells = rows[0].find_all(["th", "td"])
                    headers = [c.get_text(strip=True) for c in header_cells]
                    for r_idx, row in enumerate(rows[1:]):
                        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
                        if any(cells):
                            sections.append(_row_section(headers, cells, len(sections), r_idx))
            except Exception:
                pass
            return sections or _doc.structure(text, mime_type)

        return _doc.structure(text, mime_type)
