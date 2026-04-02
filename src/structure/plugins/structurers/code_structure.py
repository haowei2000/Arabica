import re

from structure.core.interfaces.structurer import BaseStructurer
from structure.plugins.structurers import register_structurer


@register_structurer
class CodeStructurer(BaseStructurer):
    name = "code"
    description = "Function/class and fenced code block extraction"

    def structure(self, text: str, mime_type: str) -> list[dict]:
        from structure.plugins.structurers.document import DocumentStructurer
        _doc = DocumentStructurer()

        sections: list[dict] = []

        # ── Markdown: fenced code blocks ─────────────────────────────────
        if mime_type == "text/markdown":
            lines = text.splitlines()
            current_heading = ""
            current_heading_level = 0
            in_block = False
            block_lang = ""
            block_lines: list[str] = []
            block_count = 0
            pending_lines: list[str] = []

            for line in lines:
                if not in_block:
                    m = re.match(r"^```(\w*)", line)
                    if m:
                        in_block = True
                        block_lang = m.group(1) or "text"
                        block_lines = []
                        ctx = "\n".join(pending_lines).strip()
                        pending_lines = []
                        hm = re.match(r"^(#{1,4})\s+(.*)", ctx.splitlines()[-1]) if ctx else None
                        if hm:
                            current_heading = hm.group(2).strip()
                            current_heading_level = len(hm.group(1))
                    else:
                        hm = re.match(r"^(#{1,4})\s+(.*)", line)
                        if hm:
                            current_heading = hm.group(2).strip()
                            current_heading_level = len(hm.group(1))
                        pending_lines.append(line)
                else:
                    if line.strip() == "```":
                        in_block = False
                        block_count += 1
                        code_content = "\n".join(block_lines)
                        lang_tag = f"[{block_lang}] " if block_lang and block_lang != "text" else ""
                        title = (lang_tag + current_heading) if current_heading else f"{lang_tag}Block {block_count}"
                        sections.append({
                            "title": title[:80], "level": current_heading_level or 1,
                            "content": f"```{block_lang}\n{code_content}\n```",
                            "position": len(sections), "structure_type": "code",
                            "code_language": block_lang,
                            "context_heading": current_heading,
                        })
                    else:
                        block_lines.append(line)

            if sections:
                return sections

        # ── Plain text: detect function/class definitions ─────────────────
        LANG_PATTERNS: list[tuple[str, str]] = [
            ("python",     r"^(class\s+\w+[\w\s\(\),]*:|(?:async\s+)?def\s+\w+\s*\()"),
            ("typescript", r"^(?:export\s+)?(?:(?:async\s+)?function\s+\w+|class\s+\w+|(?:const|let)\s+\w+\s*=\s*(?:async\s+)?\()"),
            ("javascript", r"^(?:(?:async\s+)?function\s+\w+|class\s+\w+|(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?\()"),
            ("java",       r"^(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+\w+\s*\("),
            ("go",         r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?\w+\s*\("),
        ]

        detected_lang = ""
        detected_matches: list = []
        for lang, pattern in LANG_PATTERNS:
            matches = list(re.finditer(pattern, text, re.MULTILINE))
            if len(matches) > len(detected_matches):
                detected_lang = lang
                detected_matches = matches

        if len(detected_matches) >= 2:
            positions = [m.start() for m in detected_matches] + [len(text)]
            preamble = text[:positions[0]].strip()
            if preamble:
                sections.append({
                    "title": "Preamble / imports", "level": 1, "content": preamble,
                    "position": 0, "structure_type": "code", "code_language": detected_lang,
                })

            current_class: str | None = None
            for idx, match in enumerate(detected_matches):  # noqa: B007
                snippet = text[positions[idx]:positions[idx + 1]].strip()
                first_line = snippet.splitlines()[0]
                is_class = bool(re.match(r"^class\s+", first_line))
                level = 1 if is_class else 2

                if is_class:
                    cm = re.match(r"^class\s+(\w+)", first_line)
                    current_class = cm.group(1) if cm else None
                    title = f"class {current_class}" if current_class else first_line[:80]
                else:
                    fm = re.search(r"(?:def|func|function)\s+(\w+)|(?:const|let|var)\s+(\w+)", first_line)
                    func_name = (fm.group(1) or fm.group(2)) if fm else first_line[:40]
                    title = f"{current_class}.{func_name}" if current_class else func_name

                sections.append({
                    "title": title[:80], "level": level, "content": snippet,
                    "position": len(sections), "structure_type": "code",
                    "code_language": detected_lang,
                })
            return sections

        return _doc.structure(text, mime_type)
