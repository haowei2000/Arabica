"""Text structurize tool.

Analyzes arbitrary text (plain prose, Markdown, source code) and returns
a structured representation with elements like paragraphs, headings,
functions, classes, imports, etc.
"""

import ast
import re
from typing import Any

from pydantic import Field

from aiwen.interfaces.tool import InnerTool, ToolInputSchema, ToolMetadata, ToolOutputSchema

MAX_TEXT_SIZE = 500_000  # 500 KB

# ═══════════════════════════════════════════════════════════════════════════════
# LANGUAGE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

_LANGUAGE_SIGNALS: dict[str, list[tuple[re.Pattern[str], int]]] = {
    "python": [
        (re.compile(r"^\s*def \w+\(", re.MULTILINE), 3),
        (re.compile(r"^\s*class \w+.*:", re.MULTILINE), 3),
        (re.compile(r"^\s*import \w+", re.MULTILINE), 2),
        (re.compile(r"^\s*from \w+ import", re.MULTILINE), 2),
        (re.compile(r"^\s*@\w+", re.MULTILINE), 1),
        (re.compile(r"^\s*if __name__\s*==", re.MULTILINE), 3),
        (re.compile(r":\s*$", re.MULTILINE), 1),
        (re.compile(r"^\s*async def ", re.MULTILINE), 3),
        (re.compile(r"^\s*await ", re.MULTILINE), 2),
    ],
    "javascript": [
        (re.compile(r"\bfunction\s+\w+\s*\(", re.MULTILINE), 3),
        (re.compile(r"\bconst\s+\w+\s*=", re.MULTILINE), 2),
        (re.compile(r"\blet\s+\w+\s*=", re.MULTILINE), 2),
        (re.compile(r"\bvar\s+\w+\s*=", re.MULTILINE), 1),
        (re.compile(r"=>\s*[{(]", re.MULTILINE), 2),
        (re.compile(r"\brequire\s*\(", re.MULTILINE), 3),
        (re.compile(r"\bmodule\.exports\b", re.MULTILINE), 3),
        (re.compile(r"\bconsole\.(log|error|warn)\b", re.MULTILINE), 1),
    ],
    "typescript": [
        (re.compile(r"\binterface\s+\w+", re.MULTILINE), 4),
        (re.compile(r":\s*(string|number|boolean|any|void)\b", re.MULTILINE), 3),
        (re.compile(r"\btype\s+\w+\s*=", re.MULTILINE), 3),
        (re.compile(r"<\w+>", re.MULTILINE), 1),
        (re.compile(r"\bas\s+\w+", re.MULTILINE), 2),
        (re.compile(r"\bconst\s+\w+\s*:", re.MULTILINE), 2),
        (re.compile(r"\bfunction\s+\w+\s*\(", re.MULTILINE), 1),
    ],
    "java": [
        (re.compile(r"\bpublic\s+class\s+\w+", re.MULTILINE), 4),
        (re.compile(r"\bprivate\s+\w+\s+\w+", re.MULTILINE), 2),
        (re.compile(r"\bpackage\s+[\w.]+;", re.MULTILINE), 4),
        (re.compile(r"\bimport\s+[\w.]+;", re.MULTILINE), 3),
        (re.compile(r"\bSystem\.out\.print", re.MULTILINE), 3),
        (re.compile(r"\bpublic\s+static\s+void\s+main", re.MULTILINE), 5),
        (re.compile(r"\b@Override\b", re.MULTILINE), 3),
    ],
    "go": [
        (re.compile(r"^package\s+\w+", re.MULTILINE), 5),
        (re.compile(r"^func\s+", re.MULTILINE), 3),
        (re.compile(r"\bfmt\.\w+", re.MULTILINE), 2),
        (re.compile(r":=", re.MULTILINE), 2),
        (re.compile(r"^import\s+\(", re.MULTILINE), 3),
        (re.compile(r"\bstruct\s*\{", re.MULTILINE), 3),
        (re.compile(r"\binterface\s*\{", re.MULTILINE), 3),
    ],
    "rust": [
        (re.compile(r"\bfn\s+\w+\s*\(", re.MULTILINE), 4),
        (re.compile(r"\blet\s+mut\s+", re.MULTILINE), 4),
        (re.compile(r"\bimpl\s+\w+", re.MULTILINE), 4),
        (re.compile(r"\buse\s+[\w:]+", re.MULTILINE), 3),
        (re.compile(r"\bpub\s+(fn|struct|enum|mod)\b", re.MULTILINE), 4),
        (re.compile(r"->", re.MULTILINE), 1),
        (re.compile(r"\bmatch\s+\w+", re.MULTILINE), 2),
        (re.compile(r"#\[derive\(", re.MULTILINE), 4),
    ],
    "c": [
        (re.compile(r"^#include\s*[<\"]", re.MULTILINE), 4),
        (re.compile(r"\bint\s+main\s*\(", re.MULTILINE), 4),
        (re.compile(r"\bprintf\s*\(", re.MULTILINE), 2),
        (re.compile(r"\bmalloc\s*\(", re.MULTILINE), 3),
        (re.compile(r"\bfree\s*\(", re.MULTILINE), 2),
        (re.compile(r"\btypedef\s+struct\b", re.MULTILINE), 3),
        (re.compile(r"\bsizeof\s*\(", re.MULTILINE), 2),
    ],
    "cpp": [
        (re.compile(r"^#include\s*[<\"]", re.MULTILINE), 2),
        (re.compile(r"\bstd::", re.MULTILINE), 4),
        (re.compile(r"\bcout\s*<<", re.MULTILINE), 4),
        (re.compile(r"\btemplate\s*<", re.MULTILINE), 4),
        (re.compile(r"\bnamespace\s+\w+", re.MULTILINE), 3),
        (re.compile(r"\bclass\s+\w+\s*[:{]", re.MULTILINE), 2),
        (re.compile(r"\bnew\s+\w+", re.MULTILINE), 1),
        (re.compile(r"\bvirtual\s+", re.MULTILINE), 3),
    ],
    "markdown": [
        (re.compile(r"^#{1,6}\s+", re.MULTILINE), 3),
        (re.compile(r"^\s*[-*+]\s+", re.MULTILINE), 1),
        (re.compile(r"^\s*\d+\.\s+", re.MULTILINE), 1),
        (re.compile(r"```\w*\n", re.MULTILINE), 3),
        (re.compile(r"\[.+?\]\(.+?\)", re.MULTILINE), 2),
        (re.compile(r"!\[.*?\]\(.+?\)", re.MULTILINE), 2),
        (re.compile(r"^\s*>\s+", re.MULTILINE), 2),
        (re.compile(r"\*\*.+?\*\*", re.MULTILINE), 1),
    ],
}

_LANGUAGE_PRIORITY = [
    "python", "javascript", "typescript", "java",
    "go", "rust", "markdown", "c", "cpp",
]


def _detect_language(text: str) -> str:
    """Detect the language of the given text using heuristic regex scoring."""
    scores: dict[str, int] = {}
    for lang, signals in _LANGUAGE_SIGNALS.items():
        score = 0
        for pattern, weight in signals:
            matches = pattern.findall(text)
            score += len(matches) * weight
        if score > 0:
            scores[lang] = score

    if not scores:
        return "plain"

    max_score = max(scores.values())
    top = [lang for lang, s in scores.items() if s == max_score]
    if len(top) == 1:
        return top[0]
    for lang in _LANGUAGE_PRIORITY:
        if lang in top:
            return lang
    return top[0]


# ═══════════════════════════════════════════════════════════════════════════════
# STATISTICS HELPER
# ═══════════════════════════════════════════════════════════════════════════════


def _compute_statistics(text: str) -> dict[str, int]:
    """Compute basic text statistics."""
    lines = text.splitlines()
    words = text.split()
    return {
        "characters": len(text),
        "lines": len(lines),
        "words": len(words),
        "non_empty_lines": sum(1 for line in lines if line.strip()),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PLAIN TEXT PARSER
# ═══════════════════════════════════════════════════════════════════════════════

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _parse_plain_text(
    text: str,
    include_statistics: bool = True,
    include_line_ranges: bool = True,
) -> dict[str, Any]:
    """Parse plain text into paragraphs and sentences."""
    lines = text.splitlines()
    paragraphs: list[dict[str, Any]] = []

    current_lines: list[str] = []
    para_start = 0

    for i, line in enumerate(lines):
        if line.strip() == "":
            if current_lines:
                para_text = "\n".join(current_lines)
                para: dict[str, Any] = {
                    "index": len(paragraphs),
                    "text": para_text,
                    "sentences": [
                        s.strip() for s in _SENTENCE_SPLIT_RE.split(para_text) if s.strip()
                    ],
                }
                if include_line_ranges:
                    para["line_start"] = para_start + 1
                    para["line_end"] = i
                paragraphs.append(para)
                current_lines = []
        else:
            if not current_lines:
                para_start = i
            current_lines.append(line)

    if current_lines:
        para_text = "\n".join(current_lines)
        para = {
            "index": len(paragraphs),
            "text": para_text,
            "sentences": [
                s.strip() for s in _SENTENCE_SPLIT_RE.split(para_text) if s.strip()
            ],
        }
        if include_line_ranges:
            para["line_start"] = para_start + 1
            para["line_end"] = len(lines)
        paragraphs.append(para)

    result: dict[str, Any] = {
        "format": "plain",
        "paragraphs": paragraphs,
    }
    if include_statistics:
        result["statistics"] = _compute_statistics(text)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# MARKDOWN PARSER
# ═══════════════════════════════════════════════════════════════════════════════

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
_MD_CODE_BLOCK_START_RE = re.compile(r"^```(\w*)")
_MD_CODE_BLOCK_END_RE = re.compile(r"^```\s*$")
_MD_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)")
_MD_LINK_RE = re.compile(r"\[(.+?)\]\((.+?)\)")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((.+?)\)")
_MD_BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)")


def _parse_markdown(
    text: str,
    include_statistics: bool = True,
    include_line_ranges: bool = True,
) -> dict[str, Any]:
    """Parse Markdown text into structural elements."""
    lines = text.splitlines()
    elements: list[dict[str, Any]] = []
    headings_outline: list[dict[str, Any]] = []

    i = 0
    para_lines: list[str] = []
    para_start = 0

    def _flush_paragraph() -> None:
        nonlocal para_lines
        if not para_lines:
            return
        para_text = "\n".join(para_lines)
        links = [{"text": m[0], "url": m[1]} for m in _MD_LINK_RE.findall(para_text)]
        images = [{"alt": m[0], "url": m[1]} for m in _MD_IMAGE_RE.findall(para_text)]

        el: dict[str, Any] = {"type": "paragraph", "text": para_text}
        if links:
            el["links"] = links
        if images:
            el["images"] = images
        if include_line_ranges:
            el["line_start"] = para_start + 1
            el["line_end"] = para_start + len(para_lines)
        elements.append(el)
        para_lines = []

    while i < len(lines):
        line = lines[i]

        m = _MD_HEADING_RE.match(line)
        if m:
            _flush_paragraph()
            level = len(m.group(1))
            title = m.group(2).strip()
            el: dict[str, Any] = {"type": "heading", "level": level, "text": title}
            if include_line_ranges:
                el["line_start"] = i + 1
                el["line_end"] = i + 1
            elements.append(el)
            headings_outline.append({"level": level, "text": title})
            i += 1
            continue

        cm = _MD_CODE_BLOCK_START_RE.match(line)
        if cm and line.strip().startswith("```"):
            _flush_paragraph()
            lang = cm.group(1) or None
            code_lines: list[str] = []
            block_start = i
            i += 1
            while i < len(lines):
                if _MD_CODE_BLOCK_END_RE.match(lines[i]):
                    i += 1
                    break
                code_lines.append(lines[i])
                i += 1
            el = {
                "type": "code_block",
                "language": lang,
                "code": "\n".join(code_lines),
            }
            if include_line_ranges:
                el["line_start"] = block_start + 1
                el["line_end"] = i
            elements.append(el)
            continue

        bm = _MD_BLOCKQUOTE_RE.match(line)
        if bm:
            _flush_paragraph()
            quote_lines = [bm.group(1)]
            block_start = i
            i += 1
            while i < len(lines):
                bm2 = _MD_BLOCKQUOTE_RE.match(lines[i])
                if bm2:
                    quote_lines.append(bm2.group(1))
                    i += 1
                else:
                    break
            el = {"type": "blockquote", "text": "\n".join(quote_lines)}
            if include_line_ranges:
                el["line_start"] = block_start + 1
                el["line_end"] = i
            elements.append(el)
            continue

        lm = _MD_LIST_RE.match(line)
        if lm:
            _flush_paragraph()
            list_items: list[dict[str, Any]] = []
            block_start = i
            while i < len(lines):
                lm2 = _MD_LIST_RE.match(lines[i])
                if lm2:
                    indent = len(lm2.group(1))
                    marker = lm2.group(2)
                    item: dict[str, Any] = {
                        "text": lm2.group(3),
                        "indent": indent,
                    }
                    if marker[0].isdigit():
                        item["ordered"] = True
                    list_items.append(item)
                    i += 1
                elif lines[i].strip() == "":
                    break
                else:
                    break
            el = {"type": "list", "items": list_items}
            if include_line_ranges:
                el["line_start"] = block_start + 1
                el["line_end"] = i
            elements.append(el)
            continue

        if line.strip() == "":
            _flush_paragraph()
            i += 1
            continue

        if not para_lines:
            para_start = i
        para_lines.append(line)
        i += 1

    _flush_paragraph()

    result: dict[str, Any] = {
        "format": "markdown",
        "elements": elements,
        "headings_outline": headings_outline,
    }
    if include_statistics:
        result["statistics"] = _compute_statistics(text)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# PYTHON CODE PARSER (AST-based)
# ═══════════════════════════════════════════════════════════════════════════════


def _parse_python_code(
    text: str,
    include_statistics: bool = True,
    include_line_ranges: bool = True,
) -> dict[str, Any]:
    """Parse Python source code using the ast module. Falls back to generic on SyntaxError."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _parse_generic_code(text, "python", include_statistics, include_line_ranges)

    lines = text.splitlines()
    imports: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    top_level_variables: list[dict[str, Any]] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                entry: dict[str, Any] = {"module": alias.name}
                if alias.asname:
                    entry["alias"] = alias.asname
                if include_line_ranges:
                    entry["line_start"] = node.lineno
                    entry["line_end"] = node.end_lineno or node.lineno
                imports.append(entry)

        elif isinstance(node, ast.ImportFrom):
            names = [
                {"name": a.name, "alias": a.asname}
                if a.asname else {"name": a.name}
                for a in node.names
            ]
            entry = {
                "module": node.module or "",
                "names": names,
            }
            if include_line_ranges:
                entry["line_start"] = node.lineno
                entry["line_end"] = node.end_lineno or node.lineno
            imports.append(entry)

        elif isinstance(node, ast.ClassDef):
            methods: list[dict[str, Any]] = []
            for item in ast.iter_child_nodes(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = [
                        a.arg for a in item.args.args
                        if a.arg != "self" and a.arg != "cls"
                    ]
                    meth: dict[str, Any] = {
                        "name": item.name,
                        "args": args,
                        "is_async": isinstance(item, ast.AsyncFunctionDef),
                    }
                    decorators = [_decorator_name(d) for d in item.decorator_list]
                    if decorators:
                        meth["decorators"] = decorators
                    if include_line_ranges:
                        meth["line_start"] = item.lineno
                        meth["line_end"] = item.end_lineno or item.lineno
                    methods.append(meth)

            bases = [_node_name(b) for b in node.bases]
            cls_entry: dict[str, Any] = {
                "name": node.name,
                "bases": bases,
                "methods": methods,
            }
            decorators = [_decorator_name(d) for d in node.decorator_list]
            if decorators:
                cls_entry["decorators"] = decorators
            if include_line_ranges:
                cls_entry["line_start"] = node.lineno
                cls_entry["line_end"] = node.end_lineno or node.lineno
            classes.append(cls_entry)

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            fn_entry: dict[str, Any] = {
                "name": node.name,
                "args": args,
                "is_async": isinstance(node, ast.AsyncFunctionDef),
            }
            decorators = [_decorator_name(d) for d in node.decorator_list]
            if decorators:
                fn_entry["decorators"] = decorators
            if include_line_ranges:
                fn_entry["line_start"] = node.lineno
                fn_entry["line_end"] = node.end_lineno or node.lineno
            functions.append(fn_entry)

        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    name = _node_name(target)
                    if name:
                        var: dict[str, Any] = {"name": name}
                        if include_line_ranges:
                            var["line_start"] = node.lineno
                            var["line_end"] = node.end_lineno or node.lineno
                        top_level_variables.append(var)
            elif isinstance(node, ast.AnnAssign) and node.target:
                name = _node_name(node.target)
                if name:
                    var = {"name": name}
                    if include_line_ranges:
                        var["line_start"] = node.lineno
                        var["line_end"] = node.end_lineno or node.lineno
                    top_level_variables.append(var)

    comments = _extract_comments_hash(lines, include_line_ranges)

    result: dict[str, Any] = {
        "format": "code",
        "language": "python",
        "imports": imports,
        "classes": classes,
        "functions": functions,
        "comments": comments,
        "top_level_variables": top_level_variables,
    }
    if include_statistics:
        stats = _compute_statistics(text)
        stats["import_count"] = len(imports)
        stats["class_count"] = len(classes)
        stats["function_count"] = len(functions)
        result["statistics"] = stats
    return result


def _node_name(node: ast.AST) -> str:
    """Extract a simple name string from an AST node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _node_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Subscript):
        return _node_name(node.value)
    return ""


def _decorator_name(node: ast.AST) -> str:
    """Extract decorator name from an AST node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _decorator_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def _extract_comments_hash(
    lines: list[str], include_line_ranges: bool
) -> list[dict[str, Any]]:
    """Extract # comments from source lines."""
    comments: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            entry: dict[str, Any] = {"text": stripped.lstrip("# ").strip()}
            if include_line_ranges:
                entry["line"] = i + 1
            comments.append(entry)
    return comments


# ═══════════════════════════════════════════════════════════════════════════════
# GENERIC CODE PARSER (regex-based, multi-language)
# ═══════════════════════════════════════════════════════════════════════════════

_LANG_PATTERNS: dict[str, dict[str, re.Pattern[str] | None]] = {
    "javascript": {
        "import": re.compile(
            r"^(?:import\s+.+?from\s+['\"].+?['\"]|"
            r"const\s+.+?=\s*require\s*\(['\"].+?['\"]\))",
            re.MULTILINE,
        ),
        "class": re.compile(r"^\s*(?:export\s+)?class\s+(\w+)", re.MULTILINE),
        "function": re.compile(
            r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)",
            re.MULTILINE,
        ),
        "arrow_function": re.compile(
            r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>",
            re.MULTILINE,
        ),
        "single_comment": re.compile(r"^\s*//(.*)$", re.MULTILINE),
        "multi_comment_start": re.compile(r"/\*"),
        "multi_comment_end": re.compile(r"\*/"),
        "variable": re.compile(
            r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?!.*(?:function|=>|\bclass\b))",
            re.MULTILINE,
        ),
    },
    "typescript": {
        "import": re.compile(
            r"^import\s+.+?from\s+['\"].+?['\"]", re.MULTILINE
        ),
        "class": re.compile(r"^\s*(?:export\s+)?class\s+(\w+)", re.MULTILINE),
        "interface": re.compile(
            r"^\s*(?:export\s+)?interface\s+(\w+)", re.MULTILINE
        ),
        "function": re.compile(
            r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)",
            re.MULTILINE,
        ),
        "arrow_function": re.compile(
            r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::\s*\w+(?:<[^>]+>)?)?\s*=\s*(?:async\s+)?\([^)]*\)\s*=>",
            re.MULTILINE,
        ),
        "type_alias": re.compile(
            r"^\s*(?:export\s+)?type\s+(\w+)\s*=", re.MULTILINE
        ),
        "single_comment": re.compile(r"^\s*//(.*)$", re.MULTILINE),
        "multi_comment_start": re.compile(r"/\*"),
        "multi_comment_end": re.compile(r"\*/"),
        "variable": re.compile(
            r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::\s*\w+)?\s*=\s*(?!.*(?:function|=>|\bclass\b))",
            re.MULTILINE,
        ),
    },
    "java": {
        "import": re.compile(r"^import\s+[\w.]+;", re.MULTILINE),
        "package": re.compile(r"^package\s+([\w.]+);", re.MULTILINE),
        "class": re.compile(
            r"^\s*(?:public|private|protected)?\s*(?:abstract\s+)?class\s+(\w+)",
            re.MULTILINE,
        ),
        "interface": re.compile(
            r"^\s*(?:public\s+)?interface\s+(\w+)", re.MULTILINE
        ),
        "function": re.compile(
            r"^\s*(?:public|private|protected)\s+(?:static\s+)?(?:\w+(?:<[^>]+>)?)\s+(\w+)\s*\(([^)]*)\)",
            re.MULTILINE,
        ),
        "single_comment": re.compile(r"^\s*//(.*)$", re.MULTILINE),
        "multi_comment_start": re.compile(r"/\*"),
        "multi_comment_end": re.compile(r"\*/"),
    },
    "go": {
        "import": re.compile(r"^import\s+(?:\([\s\S]*?\)|\"[^\"]+\")", re.MULTILINE),
        "package": re.compile(r"^package\s+(\w+)", re.MULTILINE),
        "struct": re.compile(r"^type\s+(\w+)\s+struct\s*\{", re.MULTILINE),
        "interface": re.compile(r"^type\s+(\w+)\s+interface\s*\{", re.MULTILINE),
        "function": re.compile(
            r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(([^)]*)\)",
            re.MULTILINE,
        ),
        "single_comment": re.compile(r"^\s*//(.*)$", re.MULTILINE),
        "multi_comment_start": re.compile(r"/\*"),
        "multi_comment_end": re.compile(r"\*/"),
        "variable": re.compile(
            r"^var\s+(\w+)\s+", re.MULTILINE
        ),
    },
    "rust": {
        "import": re.compile(r"^use\s+[\w:]+", re.MULTILINE),
        "struct": re.compile(r"^\s*(?:pub\s+)?struct\s+(\w+)", re.MULTILINE),
        "enum": re.compile(r"^\s*(?:pub\s+)?enum\s+(\w+)", re.MULTILINE),
        "trait": re.compile(r"^\s*(?:pub\s+)?trait\s+(\w+)", re.MULTILINE),
        "impl": re.compile(r"^\s*impl(?:<[^>]+>)?\s+(\w+)", re.MULTILINE),
        "function": re.compile(
            r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*\(([^)]*)\)",
            re.MULTILINE,
        ),
        "single_comment": re.compile(r"^\s*//(.*)$", re.MULTILINE),
        "multi_comment_start": re.compile(r"/\*"),
        "multi_comment_end": re.compile(r"\*/"),
        "variable": re.compile(
            r"^\s*(?:pub\s+)?(?:static|const)\s+(\w+)\s*:", re.MULTILINE
        ),
    },
    "c": {
        "import": re.compile(r"^#include\s*[<\"]([^>\"]+)[>\"]", re.MULTILINE),
        "struct": re.compile(r"^\s*(?:typedef\s+)?struct\s+(\w+)", re.MULTILINE),
        "function": re.compile(
            r"^\s*(?:static\s+)?(?:inline\s+)?(?:const\s+)?\w[\w\s*]+\s+(\w+)\s*\(([^)]*)\)\s*\{",
            re.MULTILINE,
        ),
        "single_comment": re.compile(r"^\s*//(.*)$", re.MULTILINE),
        "multi_comment_start": re.compile(r"/\*"),
        "multi_comment_end": re.compile(r"\*/"),
        "define": re.compile(r"^#define\s+(\w+)", re.MULTILINE),
    },
    "cpp": {
        "import": re.compile(r"^#include\s*[<\"]([^>\"]+)[>\"]", re.MULTILINE),
        "namespace": re.compile(r"^\s*namespace\s+(\w+)", re.MULTILINE),
        "class": re.compile(r"^\s*class\s+(\w+)", re.MULTILINE),
        "struct": re.compile(r"^\s*(?:typedef\s+)?struct\s+(\w+)", re.MULTILINE),
        "function": re.compile(
            r"^\s*(?:virtual\s+)?(?:static\s+)?(?:inline\s+)?(?:const\s+)?\w[\w\s*:&<>]+\s+(\w+)\s*\(([^)]*)\)\s*(?:const\s*)?(?:override\s*)?(?:=\s*0\s*)?\{",
            re.MULTILINE,
        ),
        "single_comment": re.compile(r"^\s*//(.*)$", re.MULTILINE),
        "multi_comment_start": re.compile(r"/\*"),
        "multi_comment_end": re.compile(r"\*/"),
        "define": re.compile(r"^#define\s+(\w+)", re.MULTILINE),
        "template": re.compile(r"^\s*template\s*<([^>]+)>", re.MULTILINE),
    },
}

_LANG_PATTERNS["js"] = _LANG_PATTERNS["javascript"]
_LANG_PATTERNS["ts"] = _LANG_PATTERNS["typescript"]


def _parse_generic_code(
    text: str,
    language: str,
    include_statistics: bool = True,
    include_line_ranges: bool = True,
) -> dict[str, Any]:
    """Regex-based parser for JS/TS, Java, Go, Rust, C, C++."""
    normalized_lang = language.lower().strip()
    patterns = _LANG_PATTERNS.get(normalized_lang, {})
    lines = text.splitlines()

    imports: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []
    top_level_variables: list[dict[str, Any]] = []
    language_note: str | None = None

    if not patterns:
        language_note = f"Unknown language '{language}'. Using basic pattern matching."
        patterns = {
            "single_comment": re.compile(r"^\s*(?://|#)(.*)$", re.MULTILINE),
            "multi_comment_start": re.compile(r"/\*"),
            "multi_comment_end": re.compile(r"\*/"),
        }

    import_pat = patterns.get("import")
    if import_pat:
        for m in import_pat.finditer(text):
            entry: dict[str, Any] = {"statement": m.group(0).strip()}
            if include_line_ranges:
                entry["line"] = text[:m.start()].count("\n") + 1
            imports.append(entry)

    for kind in ("class", "struct", "interface", "enum", "trait", "impl", "namespace"):
        pat = patterns.get(kind)
        if not pat:
            continue
        for m in pat.finditer(text):
            name = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0).strip()
            cls_entry: dict[str, Any] = {"name": name, "kind": kind}
            if include_line_ranges:
                start_line = text[:m.start()].count("\n") + 1
                end_line = _find_block_end(lines, start_line - 1)
                cls_entry["line_start"] = start_line
                cls_entry["line_end"] = end_line
            classes.append(cls_entry)

    for kind in ("function", "arrow_function"):
        pat = patterns.get(kind)
        if not pat:
            continue
        for m in pat.finditer(text):
            name = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0).strip()
            fn_entry: dict[str, Any] = {"name": name}
            if kind == "arrow_function":
                fn_entry["arrow"] = True
            if m.lastindex and m.lastindex >= 2:
                args_str = m.group(2).strip()
                if args_str:
                    fn_entry["args"] = [a.strip() for a in args_str.split(",") if a.strip()]
            if include_line_ranges:
                start_line = text[:m.start()].count("\n") + 1
                end_line = _find_block_end(lines, start_line - 1)
                fn_entry["line_start"] = start_line
                fn_entry["line_end"] = end_line
            functions.append(fn_entry)

    type_pat = patterns.get("type_alias")
    if type_pat:
        for m in type_pat.finditer(text):
            name = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0).strip()
            cls_entry = {"name": name, "kind": "type_alias"}
            if include_line_ranges:
                cls_entry["line"] = text[:m.start()].count("\n") + 1
            classes.append(cls_entry)

    single_pat = patterns.get("single_comment")
    if single_pat:
        for m in single_pat.finditer(text):
            entry = {"text": m.group(1).strip()}
            if include_line_ranges:
                entry["line"] = text[:m.start()].count("\n") + 1
            comments.append(entry)

    multi_start_pat = patterns.get("multi_comment_start")
    multi_end_pat = patterns.get("multi_comment_end")
    if multi_start_pat and multi_end_pat:
        for m in multi_start_pat.finditer(text):
            end_m = multi_end_pat.search(text, m.end())
            if end_m:
                comment_text = text[m.start():end_m.end()].strip()
                entry = {"text": comment_text, "multiline": True}
                if include_line_ranges:
                    entry["line_start"] = text[:m.start()].count("\n") + 1
                    entry["line_end"] = text[:end_m.end()].count("\n") + 1
                comments.append(entry)

    var_pat = patterns.get("variable")
    if var_pat:
        for m in var_pat.finditer(text):
            name = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0).strip()
            var_entry: dict[str, Any] = {"name": name}
            if include_line_ranges:
                var_entry["line"] = text[:m.start()].count("\n") + 1
            top_level_variables.append(var_entry)

    define_pat = patterns.get("define")
    if define_pat:
        for m in define_pat.finditer(text):
            name = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0).strip()
            var_entry = {"name": name, "kind": "define"}
            if include_line_ranges:
                var_entry["line"] = text[:m.start()].count("\n") + 1
            top_level_variables.append(var_entry)

    pkg_pat = patterns.get("package")
    if pkg_pat:
        m = pkg_pat.search(text)
        if m:
            pkg_name = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0).strip()
            imports.insert(0, {"statement": f"package {pkg_name}", "kind": "package"})

    result: dict[str, Any] = {
        "format": "code",
        "language": normalized_lang,
        "imports": imports,
        "classes": classes,
        "functions": functions,
        "comments": comments,
        "top_level_variables": top_level_variables,
    }
    if language_note:
        result["language_note"] = language_note
    if include_statistics:
        stats = _compute_statistics(text)
        stats["import_count"] = len(imports)
        stats["class_count"] = len(classes)
        stats["function_count"] = len(functions)
        result["statistics"] = stats
    return result


def _find_block_end(lines: list[str], start_idx: int) -> int:
    """Find the end of a brace-delimited block starting at start_idx."""
    depth = 0
    found_open = False

    for i in range(start_idx, len(lines)):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
                found_open = True
            elif ch == "}":
                depth -= 1
                if found_open and depth == 0:
                    return i + 1

    return start_idx + 1


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL CLASS
# ═══════════════════════════════════════════════════════════════════════════════

_CODE_LANGUAGES = {
    "python", "javascript", "js", "typescript", "ts",
    "java", "go", "rust", "c", "cpp",
}


class StructurizeTextTool(InnerTool):
    """Analyze and structurize arbitrary text into a structured representation."""

    METADATA = ToolMetadata(
        name="structurize_text",
        display_name="Structurize Text",
        description=(
            "Analyze arbitrary text (plain prose, Markdown, or source code) and "
            "return a structured representation. Extracts elements such as "
            "paragraphs, headings, functions, classes, imports, and more. "
            "Supports Python (AST-based), JavaScript, TypeScript, Java, Go, "
            "Rust, C, C++, Markdown, and plain text."
        ),
        category="utility",
        tags=["text", "analysis", "structure", "parse", "code"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        text: str = Field(description="The text content to structurize")
        language: str | None = Field(
            default=None,
            description=(
                "Language hint: 'plain', 'markdown', 'python', 'javascript', "
                "'typescript', 'java', 'go', 'rust', 'c', 'cpp'. "
                "Auto-detected if omitted."
            ),
        )
        include_statistics: bool = Field(
            default=True,
            description="Include word/line/character statistics in the output",
        )
        include_line_ranges: bool = Field(
            default=True,
            description="Include start/end line numbers for each extracted element",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        text = input_data.text

        if not text or not text.strip():
            return ToolOutputSchema(
                success=False,
                message="Input text is empty",
                error="empty_text",
            )

        if len(text) > MAX_TEXT_SIZE:
            return ToolOutputSchema(
                success=False,
                message=f"Text exceeds maximum size of {MAX_TEXT_SIZE} bytes ({len(text)} given)",
                error="text_too_large",
            )

        language = input_data.language.lower().strip() if input_data.language else None
        if not language:
            language = _detect_language(text)

        include_stats = input_data.include_statistics
        include_lines = input_data.include_line_ranges

        if language == "plain":
            data = _parse_plain_text(text, include_stats, include_lines)
        elif language == "markdown":
            data = _parse_markdown(text, include_stats, include_lines)
        elif language == "python":
            data = _parse_python_code(text, include_stats, include_lines)
        elif language in _CODE_LANGUAGES:
            data = _parse_generic_code(text, language, include_stats, include_lines)
        else:
            data = _parse_generic_code(text, language, include_stats, include_lines)

        return ToolOutputSchema(
            success=True,
            message=f"Text structurized as {data.get('format', language)}",
            data=data,
        )
